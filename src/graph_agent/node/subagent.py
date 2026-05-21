"""SubAgent 执行节点——在 ReAct 循环中运行 SubAgent 完成子任务。"""

from graph_agent.orchestration.prompt_loader import PromptLoader
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


def _run_subagent(config: SubAgentConfig, task_description: str,
                  task_id: str, state: OrchestrationState,
                  loader: PromptLoader) -> str:
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
        loader,
        task_description=task_description,
    ))
    llm_with_tools = llm.bind_tools(langchain_tools) if langchain_tools else llm

    messages = [SystemMessage(content=system_prompt),
                HumanMessage(content=sanitize_text(f"请完成以下任务:\n{task_description}"))]

    injected = state.get("_injected_messages")
    if injected:
        messages.extend(injected)
        state["_injected_messages"] = None

    for _ in range(config.max_iterations):
        response = llm_with_tools.invoke(messages, config={"run_name": config.name})
        messages.append(response)

        has_tool_calls = (hasattr(response, "tool_calls") and response.tool_calls)

        if not has_tool_calls:
            return response.content if hasattr(response, 'content') else str(response)

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
                        "llm_response": messages[-1] if messages else None,
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

    loader = PromptLoader()
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
            result = _run_subagent(config, task.description, task.task_id, state, loader)
        except AskUserException as e:
            full_state = dict(state)
            if e.state and e.state.get("llm_response"):
                full_state["_ask_user_llm_response"] = e.state["llm_response"]
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

