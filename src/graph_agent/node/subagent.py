"""SubAgent 执行节点——在 ReAct 循环中运行 SubAgent 完成子任务。"""

import threading
from contextlib import contextmanager

from graph_agent.orchestration.state import OrchestrationState, OrchestrationPhase
from graph_agent.orchestration.subagent import (
    SubAgentConfig, SubAgentRegistry, register_script_tools,
)
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
    else:
        messages = [SystemMessage(content=system_prompt),
                    HumanMessage(content=sanitize_text(f"请完成以下任务:\n{task_description}"))]

        injected = state.get("_injected_messages")
        if injected:
            messages.extend(injected)
        state["_injected_messages"] = None

    with agent_execution_context(config.name):
        first_iteration = True
        for _ in range(config.max_iterations):
            response = llm_with_tools.invoke(messages, config={"run_name": config.name})
            messages.append(response)

            has_tool_calls = (hasattr(response, "tool_calls") and response.tool_calls)

            if not has_tool_calls:
                # 第一轮有工具可用但未调用：注入提醒并重试，防止 LLM 虚构结果
                if first_iteration and langchain_tools:
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
                        e.state = {
                            "messages": list(messages),
                            "ask_user_tool_id": tool_id,
                        }
                        raise
                    except Exception as e:
                        result_text = f"工具执行错误: {e}"
                else:
                    result_text = f"错误: 工具 '{tool_name}' 不可用"

                messages.append(ToolMessage(content=result_text, tool_call_id=tool_id, name=tool_name))

    return messages[-1].content if messages else "执行超限，未获得结果"


def subagent_exec_node(state: OrchestrationState) -> dict:
    """SubAgent 执行节点：串行执行所有 running 状态的子任务。"""
    from graph_agent.acp.checkpoint import _check_interrupt

    task_plan = state.get("task_plan")
    if not task_plan:
        return {}

    get_tracer().trace_phase("子任务执行", "SubAgent", "串行执行所有就绪的子任务")

    sub_results = dict(state.get("sub_results", {}))
    result_messages = []

    for task in task_plan.sub_tasks:
        if task.status != "running":
            continue

        config = None
        if task.assigned_agent:
            try:
                config = _registry.get(task.assigned_agent)
            except KeyError:
                pass

        if config is None:
            task.status = "failed"
            task.error = f"未找到 SubAgent: {task.assigned_agent}"
            continue

        try:
            result = _run_subagent(config, task.description, task.task_id, state)
        except AskUserException as e:
            full_state = dict(state)
            if e.state and e.state.get("messages"):
                full_state["_subagent_messages"] = e.state["messages"]
            if e.state and e.state.get("ask_user_tool_id"):
                full_state["_ask_user_tool_id"] = e.state["ask_user_tool_id"]
            full_state["task_plan"] = task_plan
            full_state["sub_results"] = sub_results
            raise AskUserException(
                question=e.question,
                options=e.options,
                require_approval=e.require_approval,
                state=full_state,
            ) from e

        sub_results[task.task_id] = result
        task.status = "completed"
        task.result = result

        msg = create_assistant_message(
            content=result,
            name=config.name,
            message_type=MessageType.SUBAGENT_TASK_RESULT,
            metadata={
                "task_id": task.task_id,
                "agent_name": config.name,
            },
        )
        result_messages.append(msg)

        _check_interrupt(state)

    has_pending = any(t.status == "pending" for t in task_plan.sub_tasks)

    _check_interrupt(state)

    return {
        "phase": OrchestrationPhase.TASK_EXECUTION if has_pending else OrchestrationPhase.RESULT_SYNTHESIS,
        "sub_results": sub_results,
        "task_plan": task_plan,
        "ga_messages": result_messages,
        "messages": agent_messages_to_langchain(result_messages),
    }
