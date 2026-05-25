"""SubAgent 执行节点——在 ReAct 循环中运行 SubAgent 完成子任务。

采用拓扑分层并行执行：按依赖关系将 running 任务分组为多个层级，
同一层内的任务无相互依赖，通过 ThreadPoolExecutor 并行执行。
"""

import concurrent.futures
import re
import threading
from contextlib import contextmanager

from graph_agent.orchestration.state import OrchestrationState, OrchestrationPhase
from graph_agent.orchestration.subagent import (
    SubAgentConfig, SubAgentRegistry, register_script_tools,
)
from graph_agent.orchestration.dag import topological_layers
from graph_agent.orchestration.context_utils import get_subagent_context_builder
from graph_agent.tools import ToolCenter
from graph_agent.mcp import MCPManager
from graph_agent.message import (
    create_assistant_message,
    agent_messages_to_langchain,
)
from graph_agent.message.message_type import MessageType
from graph_agent.tracer import get_tracer
from graph_agent.acp.checkpoint import AskUserException

_tool_center = ToolCenter()
_tool_center.auto_discover()

# 注册 MCP 工具（在 SubAgentRegistry 初始化前，确保所有工具就绪）
_mcp_manager = MCPManager(_tool_center)
_mcp_manager.setup()

# 全局 SubAgentRegistry 单例，由 SkillRegister 驱动
_registry = SubAgentRegistry()

# Agent 身份上下文（线程局部存储）
_agent_context = threading.local()

# 并行执行的最大线程数上限
_MAX_PARALLEL_WORKERS = 8


def get_current_agent_name() -> str | None:
    """获取当前线程的 Agent 身份。"""
    return getattr(_agent_context, 'name', None)


def set_current_agent_name(name: str):
    """设置当前线程的 Agent 身份。"""
    _agent_context.name = name


@contextmanager
def agent_execution_context(agent_name: str):
    """在 SubAgent 工具执行期间设置 Agent 身份上下文。

    用法：
        with agent_execution_context(config.name):
            # SubAgent ReAct 循环在此执行
            # 所有工具调用自动携带此 agent_name
            ...
    """
    old = get_current_agent_name()
    set_current_agent_name(agent_name)
    try:
        yield
    finally:
        if old is not None:
            set_current_agent_name(old)
        else:
            delattr(_agent_context, 'name')


def _strip_dangling_tool_calls(messages: list) -> None:
    """清理消息流末尾未完成的 tool_calls，防止 LLM API 400 错误。

    当 SubAgent 执行被中断恢复时，_subagent_messages 末尾可能残留
    一个带 tool_calls 的 AIMessage，但缺少对应的 ToolMessage 响应。
    此函数检测并移除这些悬挂的 tool_calls 消息。
    """
    if not messages:
        return
    from langchain_core.messages import AIMessage
    last = messages[-1]
    if isinstance(last, AIMessage) and hasattr(last, "tool_calls") and last.tool_calls:
        # 检查每个 tool_call 是否有对应的 ToolMessage
        seen_ids = set()
        for m in messages:
            if hasattr(m, "tool_call_id") and m.tool_call_id:
                seen_ids.add(m.tool_call_id)
        for tc in last.tool_calls:
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
            if tc_id and tc_id not in seen_ids:
                # 存在未响应的 tool_call，移除该 AIMessage 让 LLM 重试
                messages.pop()
                return


def _inject_approved_tools(state: OrchestrationState) -> None:
    """将恢复执行时已通过用户授权的工具注入 RBAC 白名单。

    从 state 中读取 _rbac_pending_escalation 令牌，
    若令牌中的 approved 字段为 True，将对应工具名加入 _approved_tools。
    """
    token = state.get("_rbac_pending_escalation")
    if not token:
        return
    state["_rbac_pending_escalation"] = None
    if token.get("approved") and token.get("tool_name"):
        from graph_agent.hook.builtin.rbac_hook import _approved_tools
        _approved_tools.add(token["tool_name"])


def _run_subagent(config: SubAgentConfig, task_description: str,
                  task_id: str, state: OrchestrationState) -> str:
    """在 ReAct 循环中运行单个 SubAgent，返回执行结果。

    每个 SubAgent 只能看见和使用其系统提示词中声明的工具。
    对于用户自定义 Skill，其 scripts/ 中的脚本会被动态注册为工具。
    """
    from graph_agent.llm import LLMFactory
    from graph_agent.session.persistence import sanitize_text
    from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

    get_tracer().trace_phase(
        f"执行子任务 [{task_id}]", config.name,
        task_description[:120],
    )

    provider = LLMFactory.create_from_env()
    llm = provider.get_chat_model()

    # 用户自定义 Skill：动态注册 scripts/ 中的脚本工具
    skill_def = config._skill_def
    registered_script_names = []
    if skill_def is not None and skill_def.meta.type == "user" and skill_def.script_files:
        registered_script_names = register_script_tools(skill_def, _tool_center)

    tool_names = set(config.tools)
    tool_names.update(registered_script_names)
    all_tools = {t.name: t for t in _tool_center.list_tools()}
    subagent_tools = [all_tools[name] for name in tool_names if name in all_tools]
    langchain_tools = [t.to_langchain_tool() for t in subagent_tools]

    system_prompt = sanitize_text(config.load_system_prompt(
        task_description=task_description,
    ))
    llm_with_tools = llm.bind_tools(langchain_tools) if langchain_tools else llm

    subagent_msgs = state.get("_subagent_messages")
    if subagent_msgs:
        messages = list(subagent_msgs)
        state["_subagent_messages"] = None
        # 清理恢复消息流末尾可能残留的未完成 tool_calls，
        # 避免 LLM API 报 400 错误（insufficient tool messages）
        _strip_dangling_tool_calls(messages)
    else:
        layer4_context = state.get("subagent_contexts", {}).get(task_id, task_description) if state.get("subagent_contexts") else task_description
        messages = [SystemMessage(content=system_prompt),
                    HumanMessage(content=sanitize_text(layer4_context))]

        injected = state.get("_injected_messages")
        if injected:
            messages.extend(injected)
        state["_injected_messages"] = None

    # 恢复执行时注入已通过 RBAC 升级授权的工具
    _inject_approved_tools(state)

    # 每次 ReAct 迭代创建独立 callback（并行 SubAgent 线程安全）
    live_push = state.get("_live_push")

    with agent_execution_context(config.name):
        first_iteration = True
        for _ in range(config.max_iterations):
            invoke_config: dict = {"run_name": config.name}
            if live_push is not None:
                from graph_agent.acp.streaming_callback import ACPStreamingCallback
                invoke_config["callbacks"] = [
                    ACPStreamingCallback(live_push, agent_name=config.name)
                ]
            response = llm_with_tools.invoke(messages, config=invoke_config)
            messages.append(response)

            has_tool_calls = (hasattr(response, "tool_calls") and response.tool_calls)

            if not has_tool_calls:
                # 首轮有工具可用但未调用：仅当任务明确需要产出物时才注入提醒
                if first_iteration and langchain_tools:
                    _output_keywords = (
                        "文件", "写入", "生成", "保存", "创建", "执行", "命令", "运行",
                        "pptx", "markdown", "json", "csv", "转换", "下载", "抓取",
                        "file", "write", "generate", "create", "save", "run",
                    )
                    # 纯文本输出任务不应触发强制工具调用
                    _text_only_markers = (
                        "文本回复", "文字回答", "聊天对话", "介绍", "列出",
                        "问候", "闲聊", "回答", "解释说明",
                    )
                    _needs_tool = (
                        any(kw in task_description for kw in _output_keywords)
                        and not any(m in task_description for m in _text_only_markers)
                    )
                    if _needs_tool:
                        from langchain_core.messages import HumanMessage as HM
                        messages.append(HM(
                            content="你刚才没有调用任何工具。请立即调用相应的工具完成实际操作，"
                                    "不要仅凭文字描述声称已完成任务。"
                        ))
                        first_iteration = False
                        continue
                return response.content if hasattr(response, 'content') else str(response)

            first_iteration = False

            for tool_call in response.tool_calls:
                tool_name = tool_call.get("name", "")
                tool_args = tool_call.get("args", {})
                tool_id = tool_call.get("id", "")

                tool = all_tools.get(tool_name)
                if tool and tool_name in tool_names:
                    try:
                        result_text = tool.run(**tool_args)
                    except AskUserException as e:
                        merged = dict(e.state) if e.state else {}
                        merged["messages"] = list(messages)
                        merged["ask_user_tool_id"] = tool_id
                        e.state = merged
                        raise
                    except Exception as e:
                        result_text = f"工具执行错误: {e}"
                else:
                    result_text = f"错误: 工具 '{tool_name}' 不可用"

                messages.append(ToolMessage(content=result_text, tool_call_id=tool_id, name=tool_name))

    return messages[-1].content if messages else "执行超限，未获得结果"


# ============================================================================
# DAG 分层并行执行
# ============================================================================


def _resolve_placeholders_for_task(
    task,
    sub_results: dict[str, str],
) -> None:
    """JIT 解析任务描述和输入数据中的 {{task_id}} 占位符。

    在任务执行前调用，使用当前已累积的 sub_results 进行替换。
    同时将解析后的 input_data 追加到任务描述中（同原 _dispatch_tasks 行为）。
    """
    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        if key.endswith(".result"):
            task_id_ref = key[:-7]
        else:
            task_id_ref = key
        return sub_results.get(task_id_ref, match.group(0))

    task.description = re.sub(r'\{\{(.+?)\}\}', _replace, task.description)
    for k, v in task.input_data.items():
        if isinstance(v, str):
            task.input_data[k] = re.sub(r'\{\{(.+?)\}\}', _replace, v)

    # 将解析后的 input_data 追加到任务描述中（幂等：恢复执行时不重复追加）
    if task.input_data and "【执行参数】" not in task.description:
        param_lines = []
        for k, v in task.input_data.items():
            v_str = str(v)
            if "\n" in v_str:
                param_lines.append(f"- {k}:\n  \"\"\"\n  {v_str}\n  \"\"\"")
            else:
                param_lines.append(f"- {k}: {v_str}")
        params_block = "\n".join(param_lines)
        task.description = f"{task.description}\n\n【执行参数】\n{params_block}"


def _execute_task(
    task,
    config: SubAgentConfig,
    local_state: dict,
) -> tuple[str, str, str | None]:
    """执行单个子任务（在工作线程中运行）。

    使用 local_state（state 浅拷贝）避免多线程竞争。
    若任务内部触发 AskUserException，直接传播到主线程处理。

    Returns:
        (task_id, result_or_empty, error_or_none)
    """
    threading.current_thread().name = f"subagent-{task.task_id}"
    try:
        result = _run_subagent(config, task.description, task.task_id, local_state)
        return (task.task_id, result, None)
    except AskUserException:
        raise  # 传播到主线程处理中断
    except Exception as e:
        return (task.task_id, "", str(e))


def _execute_layer_parallel(
    tasks: list,
    state: OrchestrationState,
) -> list[dict]:
    """并行执行一层中的所有任务。

    对每个任务：
    - 使用 state 浅拷贝确保线程安全
    - 无法找到 config 的任务直接标记失败

    Returns:
        [{"task_id": str, "result": str, "error": str, "status": str}, ...]
    """
    futures_map = {}
    worker_count = min(len(tasks), _MAX_PARALLEL_WORKERS)

    # 预处理：区分有效任务和无 config 任务
    valid_tasks = []
    skipped_results = []
    for task in tasks:
        try:
            config = _registry.get(task.assigned_agent)
        except KeyError:
            config = None
        if config is None:
            skipped_results.append({
                "task_id": task.task_id,
                "result": "",
                "error": f"未找到 SubAgent 配置: {task.assigned_agent}",
                "status": "failed",
            })
        else:
            valid_tasks.append((task, config))

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        for task, config in valid_tasks:
            local_state = dict(state)
            future = executor.submit(_execute_task, task, config, local_state)
            futures_map[future] = task

        results = list(skipped_results)
        for future in concurrent.futures.as_completed(futures_map):
            task = futures_map[future]
            try:
                task_id, result, error = future.result()
                results.append({
                    "task_id": task_id,
                    "result": result if not error else "",
                    "error": error or "",
                    "status": "failed" if error else "completed",
                })
            except AskUserException as e:
                # 取消所有未开始的任务
                for f in futures_map:
                    f.cancel()
                # 将已完成任务的部分结果附着到异常对象，防止恢复时重复执行
                e._partial_results = results
                raise

    return results


def subagent_exec_node(state: OrchestrationState) -> dict:
    """SubAgent 执行节点：按拓扑分层并行执行所有 running 状态的子任务。"""
    from graph_agent.acp.checkpoint import _check_interrupt

    task_plan = state.get("task_plan")
    if not task_plan:
        return {}

    _subagent_ctx_builder = get_subagent_context_builder()
    guard_context = state.get("guard_context", [])
    ga_messages = state.get("ga_messages", [])
    intent_analysis = next(
        (m for m in ga_messages
         if m.message_type and MessageType(m.message_type) == MessageType.GUARD_INTENT_ANALYSIS),
        None,
    )
    if state.get("subagent_contexts") is None:
        state["subagent_contexts"] = {}

    # 收集所有 running 任务
    running_tasks = [t for t in task_plan.sub_tasks if t.status == "running"]
    if not running_tasks:
        return {}

    # 已完成任务的结果（用于占位符解析和依赖判断）
    sub_results = dict(state.get("sub_results", {}))

    # 拓扑分层：同一层内的任务无相互依赖，可并行执行
    layers = topological_layers(running_tasks)

    # 保护：如果 running_tasks 非空但分层为空，说明存在未检测到的 DAG 环
    if not layers:
        get_tracer().trace_phase(
            "子任务执行", "SubAgent",
            "DAG 分层失败，将所有 running 任务标记为 failed（可能为循环依赖）",
        )
        for t in running_tasks:
            t.status = "failed"
            t.error = "DAG 分层失败：可能存在循环依赖或依赖缺失"
        return {
            "phase": OrchestrationPhase.RESULT_SYNTHESIS,
            "sub_results": sub_results,
            "task_plan": task_plan,
            "ga_messages": [],
            "messages": [],
        }

    get_tracer().trace_phase(
        "子任务执行", "SubAgent",
        f"DAG 拓扑分层并行执行: {len(layers)} 层, {len(running_tasks)} 个任务",
    )

    result_messages = []

    for layer_idx, layer in enumerate(layers):
        get_tracer().trace_phase(
            f"执行第 {layer_idx} 层",
            "SubAgent",
            f"并行执行 {len(layer)} 个任务: {[t.task_id for t in layer]}",
        )

        # 检查前置任务失败 → 级联标记失败，跳过执行
        executable_tasks = []
        for task in layer:
            failed_deps = [
                dep_id for dep_id in task.dependencies
                if any(
                    t.task_id == dep_id and t.status == "failed"
                    for t in task_plan.sub_tasks
                )
            ]
            if failed_deps:
                task.status = "failed"
                task.error = f"前置任务失败: {failed_deps}"
                sub_results[task.task_id] = ""
                result_messages.append(create_assistant_message(
                    content=f"前置任务失败，跳过执行: {failed_deps}",
                    name="SubAgent",
                    message_type=MessageType.SUBAGENT_TASK_RESULT,
                    metadata={"task_id": task.task_id, "status": "failed"},
                ))
            else:
                executable_tasks.append(task)

        if not executable_tasks:
            _check_interrupt(state)
            continue

        # JIT 解析本层每个任务的占位符（此时前置层的结果已就绪）
        for task in executable_tasks:
            _resolve_placeholders_for_task(task, sub_results)

        # 构建 Layer 4 上下文（前置层结果已就绪，依赖输入可见）
        layer_contexts = _subagent_ctx_builder.build_for_layer(
            layer_tasks=executable_tasks,
            all_tasks=task_plan.sub_tasks,
            overall_goal=task_plan.overall_goal,
            intent_analysis=intent_analysis,
            guard_context=guard_context,
            expected_output_format=task_plan.expected_output_format,
        )
        state["subagent_contexts"].update(layer_contexts)

        try:
            layer_results = _execute_layer_parallel(executable_tasks, state)
        except AskUserException as e:
            # 处理同层中已完成任务的部分结果（防止恢复时重复执行）
            partial_results = getattr(e, '_partial_results', [])
            for r in partial_results:
                tid = r["task_id"]
                sub_results[tid] = r["result"]
                pt = next((t for t in task_plan.sub_tasks if t.task_id == tid), None)
                if pt:
                    pt.status = r.get("status", "completed")
                    pt.result = r["result"]
                    if r.get("error"):
                        pt.error = r["error"]

            # 中断处理：保存当前状态到异常对象
            full_state = dict(state)
            if e.state and e.state.get("messages"):
                full_state["_subagent_messages"] = e.state["messages"]
            if e.state and e.state.get("ask_user_tool_id"):
                full_state["_ask_user_tool_id"] = e.state["ask_user_tool_id"]
            rbac_token = (e.state or {}).get("_rbac_pending_escalation")
            if rbac_token:
                full_state["_rbac_pending_escalation"] = rbac_token
            full_state["task_plan"] = task_plan
            full_state["sub_results"] = sub_results
            raise AskUserException(
                question=e.question,
                options=e.options,
                require_approval=e.require_approval,
                state=full_state,
            ) from e

        # 收集本层结果
        for r in layer_results:
            tid = r["task_id"]
            sub_results[tid] = r["result"]
            task = next(t for t in task_plan.sub_tasks if t.task_id == tid)
            task.status = r.get("status", "completed")
            task.result = r["result"]
            if r["error"]:
                task.error = r["error"]

            try:
                config = _registry.get(task.assigned_agent)
                agent_name = config.name
            except KeyError:
                agent_name = task.assigned_agent or "unknown"
            msg = create_assistant_message(
                content=r["result"] or r["error"],
                name=agent_name,
                message_type=MessageType.SUBAGENT_TASK_RESULT,
                metadata={
                    "task_id": tid,
                    "agent_name": agent_name,
                    "status": task.status,
                },
            )
            result_messages.append(msg)

        _check_interrupt(state)

    # 判断下一阶段
    has_pending = any(t.status == "pending" for t in task_plan.sub_tasks)
    has_running = any(t.status == "running" for t in task_plan.sub_tasks)

    return {
        "phase": (
            OrchestrationPhase.RESULT_SYNTHESIS
            if not has_pending and not has_running
            else OrchestrationPhase.TASK_EXECUTION
        ),
        "sub_results": sub_results,
        "task_plan": task_plan,
        "ga_messages": result_messages,
        "messages": agent_messages_to_langchain(result_messages),
    }
