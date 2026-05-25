"""PlanAgent 节点——任务分解、方案制定、派发执行、结果汇总。

PlanAgent 不可见、不可调用任何 Tool，只能看到从配置文件加载的 SubAgent 清单。

上下文：通过 PlanContextBuilder 构建 Layer 3 战术视图。
"""

import json
import re
import uuid

from graph_agent.orchestration.state import (
    OrchestrationState, OrchestrationPhase, TaskPlan, SubTask,
)
from graph_agent.orchestration.dag import validate_and_log
from graph_agent.orchestration.prompt_loader import PromptLoader
from graph_agent.orchestration.subagent import SubAgentRegistry
from graph_agent.orchestration.context_utils import (
    get_plan_context_builder,
    get_history_from_state,
)
from graph_agent.message import (
    create_assistant_message, create_system_message,
    generate_message_id,
    agent_message_to_langchain,
)
from graph_agent.message.message_type import MessageType
from graph_agent.tracer import get_tracer

_prompt_loader = PromptLoader()
_registry = SubAgentRegistry()


def _call_llm(system_prompt: str, user_text: str,
              state: OrchestrationState,
              name: str = "PlanAgent") -> str:
    """调用 LLM 并返回文本内容。"""
    from graph_agent.llm import LLMFactory
    from graph_agent.session.persistence import sanitize_text

    provider = LLMFactory.create_from_env()
    llm = provider.get_chat_model()

    # 每次 LLM 调用创建独立 callback（线程安全）
    live_push = state.get("_live_push")
    callbacks = None
    if live_push is not None:
        from graph_agent.acp.streaming_callback import ACPStreamingCallback
        callbacks = [ACPStreamingCallback(live_push, agent_name=name)]

    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [
        SystemMessage(content=sanitize_text(system_prompt)),
        HumanMessage(content=sanitize_text(user_text)),
    ]
    config = {"run_name": name}
    if callbacks:
        config["callbacks"] = callbacks
    response = llm.invoke(messages, config=config)
    return sanitize_text(response.content if hasattr(response, 'content') else str(response))


def _build_and_format_context(state: OrchestrationState) -> str:
    """构建 Layer 3 上下文并格式化为提示词可用的纯文本。"""
    builder = get_plan_context_builder()
    history = get_history_from_state(state)

    ga_messages = state.get("ga_messages", [])
    intent_analysis = next(
        (m for m in ga_messages
         if m.message_type and MessageType(m.message_type) == MessageType.GUARD_INTENT_ANALYSIS),
        None,
    )

    if history is not None:
        context = builder.build(
            history,
            intent_analysis=intent_analysis,
            subagent_registry=_registry,
            extra_messages=ga_messages,
        )
    else:
        # 非 ACP 场景回退
        context = _build_context_from_messages(state, intent_analysis)

    parts = []
    for msg in context:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        parts.append(f"[{msg.message_type}]: {content}")
    return "\n---\n".join(parts)


def _build_context_from_messages(
    state: OrchestrationState,
    intent_analysis: MessageBlock | None,
) -> list[MessageBlock]:
    """非 ACP 场景回退：从 state 构建 Layer 3 上下文。"""
    messages = list(state.get("messages", []))
    ga_messages = state.get("ga_messages", [])

    from graph_agent.message.convert import langchain_to_agent_message
    from graph_agent.session.context_filter import is_context_eligible
    from graph_agent.session.guard_context_builder import _is_rejected_proposal

    agent_msgs = []
    for m in messages:
        try:
            agent_msgs.append(langchain_to_agent_message(m))
        except Exception:
            pass

    all_msgs = agent_msgs + list(ga_messages)
    filtered = [
        m for m in all_msgs
        if m.message_type and is_context_eligible(MessageType(m.message_type))
    ]
    filtered = [m for m in filtered if not _is_rejected_proposal(m)]

    if intent_analysis is not None:
        filtered.append(intent_analysis)

    catalog_text = _registry.describe_all_for_llm()
    if catalog_text:
        from graph_agent.message import MessageBlock
        catalog_msg = MessageBlock(
            role="system",
            content=f"## 可用 SubAgent 能力清单\n{catalog_text}",
            name="SubAgentRegistry",
            message_type=MessageType.SYSTEM_NOTIFICATION.value,
            message_id="",
            metadata={"source": "subagent_catalog"},
        )
        filtered.append(catalog_msg)

    return filtered


def _format_sub_results(sub_results: dict) -> str:
    """格式化子任务结果。"""
    if not sub_results:
        return "(无已完成结果)"
    parts = [f"  {k}: {v[:300]}" for k, v in sub_results.items()]
    return "\n".join(parts)


def _parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON。"""
    import re

    cleaned = text.strip()

    # 1) 先尝试直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2) 从 markdown 代码块中提取 ```json ... ```
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', cleaned)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3) 按大括号深度提取最外层 JSON 对象
    start = cleaned.find('{')
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        json_str = cleaned[start:i + 1]
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            break

    # 4) 回退：尝试简单正则
    match = re.search(r'\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {}


def _generate_plan(state: OrchestrationState) -> dict:
    """方案制定阶段：将用户意图分解为 TaskPlan。"""
    get_tracer().trace_phase("方案制定", "PlanAgent", "将用户意图分解为可执行的子任务计划")

    system_prompt = _prompt_loader.load_with_context(
        "plan", "plan_generation",
        intent=state.get("intent", ""),
        guard_feedback=state.get("guard_feedback", ""),
        available_subagents=_registry.describe_all_for_llm(),
        conversation_context=_build_and_format_context(state),
    )
    user_text = f"请为以下意图制定任务计划:\n{state.get('intent', '')}"
    text = _call_llm(system_prompt, user_text, state, name="PlanAgent")
    result = _parse_json_response(text)

    sub_tasks_raw = result.get("sub_tasks", [])
    sub_tasks = []
    for i, st in enumerate(sub_tasks_raw):
        sub_tasks.append(SubTask(
            task_id=st.get("task_id", f"task_{i+1}"),
            description=st.get("description", ""),
            required_skill=st.get("required_skill", ""),
            dependencies=st.get("dependencies", []),
            input_data=st.get("input_data", {}),
        ))

    task_plan = TaskPlan(
        plan_id=result.get("plan_id", str(uuid.uuid4().hex[:8])),
        overall_goal=result.get("overall_goal", state.get("intent", "")),
        sub_tasks=sub_tasks,
        execution_strategy=result.get("execution_strategy", "parallel"),
        expected_output_format=result.get("expected_output_format", ""),
    )

    # DAG 合法性校验：检测循环依赖和缺失依赖
    if not validate_and_log(sub_tasks):
        get_tracer().trace_decision(
            "PlanAgent",
            "DAG 校验失败：存在循环依赖或缺失依赖，PlanAgent 需要重新生成",
        )

    msg = create_assistant_message(
        content=text,
        name="PlanAgent",
        message_type=MessageType.PLAN_PROPOSAL,
    )

    return {
        "phase": OrchestrationPhase.PLAN_REVIEW,
        "task_plan": task_plan,
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def _dispatch_tasks(state: OrchestrationState) -> dict:
    """任务派发阶段：一次性将所有 pending 任务派发为 running。

    不再逐批检查依赖是否满足——依赖关系交由 subagent_exec_node 的拓扑分层机制处理。
    占位符解析也延后到 subagent_exec_node 执行前 JIT 解析。
    """
    get_tracer().trace_phase("任务派发", "PlanAgent", "一次性派发所有 pending 子任务")

    task_plan = state.get("task_plan")
    if not task_plan:
        return {"phase": OrchestrationPhase.COMPLETED}

    sub_tasks = task_plan.sub_tasks
    dispatch_messages = []

    for task in sub_tasks:
        if task.status != "pending":
            continue

        # 检查是否有依赖任务失败 → 级联标记失败
        failed_deps = [
            dep_id for dep_id in task.dependencies
            if any(
                t.task_id == dep_id and t.status == "failed"
                for t in sub_tasks
            )
        ]
        if failed_deps:
            task.status = "failed"
            task.error = f"前置任务失败: {failed_deps}"
            continue

        # 匹配 SubAgent（同现有逻辑）
        candidates = _registry.find_by_skill(task.required_skill)
        if not candidates and task.required_skill:
            candidates = _registry.find_by_skill("general-purpose")
            if not candidates:
                candidates = _registry.find_by_skill("general")
            if not candidates:
                all_agents = _registry.list_all()
                if all_agents:
                    candidates = [all_agents[0]]
            if candidates:
                get_tracer().trace_decision(
                    "PlanAgent",
                    f"技能 '{task.required_skill}' 无精确匹配，回退到 {candidates[0].name}",
                )

        if candidates:
            task.status = "running"
            task.assigned_agent = candidates[0].name
            msg = create_assistant_message(
                content=task.description,
                name="PlanAgent",
                message_type=MessageType.PLAN_TASK_DISPATCH,
                metadata={
                    "task_id": task.task_id,
                    "required_skill": task.required_skill,
                    "assigned_agent": task.assigned_agent,
                },
            )
            dispatch_messages.append(msg)
        else:
            task.status = "failed"
            task.error = f"未找到匹配技能 '{task.required_skill}' 的 SubAgent"

    # 注：不再在此处解析占位符，改为 subagent_exec_node 执行前 JIT 解析

    # 判断下一阶段
    all_terminal = all(
        t.status in ("completed", "failed") for t in sub_tasks
    )

    return {
        "phase": (
            OrchestrationPhase.RESULT_SYNTHESIS if all_terminal
            else OrchestrationPhase.TASK_EXECUTION
        ),
        "task_plan": task_plan,
        "ga_messages": dispatch_messages if dispatch_messages else [],
        "messages": (
            [agent_message_to_langchain(m) for m in dispatch_messages]
            if dispatch_messages else []
        ),
    }


def _synthesize_results(state: OrchestrationState) -> dict:
    """结果汇总阶段：整合所有 SubAgent 的执行结果。"""
    get_tracer().trace_phase("结果汇总", "PlanAgent", "整合所有 SubAgent 的执行结果")

    task_plan = state.get("task_plan")
    sub_results = state.get("sub_results", {})

    system_prompt = _prompt_loader.load_with_context(
        "plan", "result_synthesis",
        intent=state.get("intent", ""),
        guard_feedback=state.get("guard_feedback", ""),
        task_plan=str(task_plan),
        sub_results=_format_sub_results(sub_results),
        conversation_context=_build_and_format_context(state),
    )
    user_text = f"请汇总以下子任务结果:\n{_format_sub_results(sub_results)}"
    text = _call_llm(system_prompt, user_text, state, name="PlanAgent")

    # 从 LLM 返回的 JSON 中提取最终的文本摘要
    result_json = _parse_json_response(text)
    final_answer = result_json.get("summary", text)

    msg = create_assistant_message(
        content=text,
        name="PlanAgent",
        message_type=MessageType.PLAN_RESULT_SYNTHESIS,
    )

    return {
        "phase": OrchestrationPhase.RESULT_REVIEW,
        "final_answer": final_answer,
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def plan_node(state: OrchestrationState) -> dict:
    """PlanAgent 节点：根据当前 phase 执行对应职责。"""
    from graph_agent.acp.checkpoint import _check_interrupt

    phase = state.get("phase", OrchestrationPhase.PLAN_GENERATION)

    if phase == OrchestrationPhase.PLAN_GENERATION:
        result = _generate_plan(state)
    elif phase == OrchestrationPhase.TASK_EXECUTION:
        result = _dispatch_tasks(state)
    elif phase == OrchestrationPhase.RESULT_SYNTHESIS:
        result = _synthesize_results(state)
    else:
        result = {}

    _check_interrupt(state)
    return result


def plan_router(state: OrchestrationState) -> str:
    """PlanAgent 的路由决策。

    注意：此时 state 已包含 plan_node 的返回值，
    因此 phase 反映的是 plan_node 执行*后*的状态。
    """
    phase = state.get("phase", OrchestrationPhase.PLAN_GENERATION)

    if phase == OrchestrationPhase.PLAN_REVIEW:
        # plan 刚完成方案制定 → GuardAgent 审核方案
        return "guard"

    if phase == OrchestrationPhase.TASK_EXECUTION:
        # 有 running 任务 → SubAgent 执行，否则 → PlanAgent 重新调度
        task_plan = state.get("task_plan")
        if task_plan and any(t.status == "running" for t in task_plan.sub_tasks):
            return "sub_exec"
        return "plan"

    if phase == OrchestrationPhase.RESULT_SYNTHESIS:
        # 所有子任务完成 → plan 再次执行（汇总结果）
        return "plan"

    if phase == OrchestrationPhase.RESULT_REVIEW:
        # plan 刚完成结果汇总 → GuardAgent 验收结果
        return "guard"

    if phase == OrchestrationPhase.COMPLETED:
        return "__end__"

    return "__end__"
