"""GuardAgent 节点——意图识别、方案审核、结果验收。

GuardAgent 不可见、不可调用任何 Tool，仅依赖 LLM 语义推理。

上下文：通过 GuardContextBuilder 构建 Layer 2 战略视图。
"""

import json

from graph_agent.orchestration.state import OrchestrationState, OrchestrationPhase
from graph_agent.orchestration.prompt_loader import PromptLoader
from graph_agent.orchestration.subagent import SubAgentRegistry
from graph_agent.orchestration.context_utils import (
    get_guard_context_builder,
    get_history_from_state,
)
from graph_agent.message import (
    MessageBlock, ContentBlock,
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
              name: str = "GuardAgent",
              message_type: MessageType = MessageType.AGENT_RESPONSE) -> MessageBlock:
    """调用 LLM 并返回 MessageBlock 格式的响应。"""
    from graph_agent.llm import LLMFactory
    from graph_agent.session.persistence import sanitize_text

    provider = LLMFactory.create_from_env()
    llm = provider.get_chat_model()

    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [
        SystemMessage(content=sanitize_text(system_prompt)),
        HumanMessage(content=sanitize_text(user_text)),
    ]
    response = llm.invoke(messages, config={"run_name": name})
    text = sanitize_text(response.content if hasattr(response, 'content') else str(response))

    return create_assistant_message(
        content=text,
        message_type=message_type,
        name=name,
    )


def _build_and_format_context(state: OrchestrationState) -> str:
    """构建 Layer 2 上下文，写回 state 并格式化为提示词可用的纯文本。"""
    builder = get_guard_context_builder()
    history = get_history_from_state(state)

    if history is not None:
        # ACP 场景：使用 ConversationHistory
        from graph_agent.llm import LLMFactory
        provider = LLMFactory.create_from_env()
        context = builder.build(
            history,
            llm_provider=provider,
            extra_messages=state.get("ga_messages", []),
        )
    else:
        # 非 ACP 场景（debug.py）：从 state["messages"] 构建临时上下文
        context = _build_context_from_messages(state)

    # 写回 state：供 SubAgent 执行节点构建 Layer 4 时提取相关上下文片段
    state["guard_context"] = context

    # 格式化
    parts = []
    for msg in context:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        parts.append(f"[{msg.message_type}]: {content}")
    return "\n---\n".join(parts)


def _build_context_from_messages(state: OrchestrationState) -> list[MessageBlock]:
    """非 ACP 场景回退：从 state["messages"] + ga_messages 构建上下文。

    通过 GuardContextBuilder 的过滤逻辑处理消息，
    但不依赖 ConversationHistory。
    """
    messages = list(state.get("messages", []))
    ga_messages = state.get("ga_messages", [])

    from graph_agent.message.convert import langchain_to_agent_message
    agent_msgs = []
    for m in messages:
        try:
            agent_msgs.append(langchain_to_agent_message(m))
        except Exception:
            pass

    # 合并 ga_messages（当前轮次已产生的 Agent 消息）
    all_msgs = agent_msgs + list(ga_messages)

    # 使用 Builder 的过滤和压缩逻辑
    from graph_agent.session.context_filter import is_context_eligible
    from graph_agent.session.guard_context_builder import _is_rejected_proposal
    from graph_agent.session.compressor import SessionConfig, PriorityCompressor

    filtered = [
        m for m in all_msgs
        if m.message_type and is_context_eligible(MessageType(m.message_type))
    ]
    filtered = [m for m in filtered if not _is_rejected_proposal(m)]

    return filtered


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


def _analyze_intent(state: OrchestrationState) -> dict:
    """意图识别阶段。"""
    get_tracer().trace_phase("意图分析", "GuardAgent", "分析用户输入，判断问题类型与复杂度")

    system_prompt = _prompt_loader.load_with_context(
        "guard", "intent_analysis",
        conversation_context=_build_and_format_context(state),
    )
    user_text = state.get("messages", [])[-1].content if state.get("messages") else ""
    msg = _call_llm(system_prompt, user_text, state,
                    name="GuardAgent", message_type=MessageType.GUARD_INTENT_ANALYSIS)
    result = _parse_json_response(msg.content if isinstance(msg.content, str) else "")

    return {
        "phase": OrchestrationPhase.PLAN_GENERATION,
        "intent": result.get("intent", user_text),
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def _review_plan(state: OrchestrationState) -> dict:
    """方案审核阶段。"""
    get_tracer().trace_phase("方案审核", "GuardAgent", "审核 PlanAgent 制定的任务计划")

    task_plan = state.get("task_plan")
    system_prompt = _prompt_loader.load_with_context(
        "guard", "plan_review",
        conversation_context=_build_and_format_context(state),
        task_plan=str(task_plan),
        intent=state.get("intent", ""),
        available_subagents=_registry.describe_all_for_llm(),
    )
    user_text = f"请审核以下任务计划:\n{task_plan}"
    msg = _call_llm(system_prompt, user_text, state,
                    name="GuardAgent", message_type=MessageType.GUARD_PLAN_REVIEW)
    result = _parse_json_response(msg.content if isinstance(msg.content, str) else "")

    approved = result.get("approved", True)
    decision = "通过" if approved else "驳回"
    get_tracer().trace_decision(
        "GuardAgent", f"方案审核{decision}", result.get("feedback", ""),
    )

    # 回写审核结论到对应的 PLAN_PROPOSAL 消息
    _mark_plan_review_outcome(state, result)

    return {
        "phase": OrchestrationPhase.TASK_EXECUTION if approved else OrchestrationPhase.PLAN_GENERATION,
        "plan_approved": approved,
        "guard_feedback": result.get("feedback", ""),
        "review_retries": state.get("review_retries", 0) + (0 if approved else 1),
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def _review_result(state: OrchestrationState) -> dict:
    """结果验收阶段。"""
    get_tracer().trace_phase("结果验收", "GuardAgent", "根据原始意图验收最终结果")

    sub_results = state.get("sub_results", {})
    final_result = "\n".join(f"{k}: {v}" for k, v in sub_results.items())

    system_prompt = _prompt_loader.load_with_context(
        "guard", "result_review",
        conversation_context=_build_and_format_context(state),
        final_result=final_result,
        intent=state.get("intent", ""),
    )
    user_text = f"请验收以下最终结果:\n{final_result}"
    msg = _call_llm(system_prompt, user_text, state,
                    name="GuardAgent", message_type=MessageType.GUARD_RESULT_REVIEW)
    result = _parse_json_response(msg.content if isinstance(msg.content, str) else "")

    approved = result.get("approved", True)
    decision = "通过" if approved else "驳回"
    get_tracer().trace_decision(
        "GuardAgent", f"结果验收{decision}", result.get("feedback", ""),
    )

    # 回写审核结论到对应的 PLAN_RESULT_SYNTHESIS 消息
    _mark_result_review_outcome(state, result)

    new_retries = state.get("review_retries", 0) + (0 if approved else 1)
    max_retries = state.get("max_review_retries", 3)
    exceeded = new_retries >= max_retries

    if exceeded and not approved:
        get_tracer().trace_decision(
            "GuardAgent", f"重试次数已达上限({max_retries})，强制结束",
        )

    return {
        "phase": OrchestrationPhase.COMPLETED if (approved or exceeded) else OrchestrationPhase.RESULT_SYNTHESIS,
        "result_approved": approved,
        "guard_feedback": result.get("feedback", ""),
        "review_retries": new_retries,
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def _find_latest_message(state: OrchestrationState, msg_type: MessageType) -> MessageBlock | None:
    """从 ga_messages 中按类型查找最新一条消息。"""
    for m in reversed(state.get("ga_messages", [])):
        if m.message_type and MessageType(m.message_type) == msg_type:
            return m
    return None


def _mark_plan_review_outcome(state: OrchestrationState, review_result: dict) -> None:
    """将 GuardAgent 方案审核结论回写到 PLAN_PROPOSAL 消息。"""
    plan_msg = _find_latest_message(state, MessageType.PLAN_PROPOSAL)
    if plan_msg is not None:
        if plan_msg.metadata is None:
            plan_msg.metadata = {}
        plan_msg.metadata["approved"] = review_result.get("approved", True)
        plan_msg.metadata["review_feedback"] = review_result.get("feedback", "")


def _mark_result_review_outcome(state: OrchestrationState, review_result: dict) -> None:
    """将 GuardAgent 结果验收结论回写到 PLAN_RESULT_SYNTHESIS 消息。"""
    synth_msg = _find_latest_message(state, MessageType.PLAN_RESULT_SYNTHESIS)
    if synth_msg is not None:
        if synth_msg.metadata is None:
            synth_msg.metadata = {}
        synth_msg.metadata["approved"] = review_result.get("approved", True)
        synth_msg.metadata["review_feedback"] = review_result.get("feedback", "")


def guard_node(state: OrchestrationState) -> dict:
    """GuardAgent 节点：根据当前 phase 执行对应职责。"""
    from graph_agent.acp.checkpoint import _check_interrupt

    phase = state.get("phase", OrchestrationPhase.INTENT_ANALYSIS)

    if phase == OrchestrationPhase.INTENT_ANALYSIS:
        result = _analyze_intent(state)
    elif phase == OrchestrationPhase.PLAN_REVIEW:
        result = _review_plan(state)
    elif phase == OrchestrationPhase.RESULT_REVIEW:
        result = _review_result(state)
    else:
        result = {}

    _check_interrupt(state)
    return result


def guard_router(state: OrchestrationState) -> str:
    """GuardAgent 的路由决策。

    注意：此时 state 已包含 guard_node 的返回值，
    因此 phase 反映的是 guard_node 执行*后*的状态。
    """
    phase = state.get("phase", OrchestrationPhase.INTENT_ANALYSIS)

    if phase == OrchestrationPhase.PLAN_GENERATION:
        # guard 刚驳回方案，检查重试次数避免死循环
        max_retries = state.get("max_review_retries", 3)
        if state.get("review_retries", 0) >= max_retries:
            return "__end__"
        return "plan"

    if phase == OrchestrationPhase.PLAN_REVIEW:
        # guard 刚完成方案审核 → PlanAgent 执行或修订
        return "plan"

    if phase == OrchestrationPhase.TASK_EXECUTION:
        # guard 审核通过方案 → PlanAgent 派发任务
        return "plan"

    if phase == OrchestrationPhase.RESULT_SYNTHESIS:
        # guard 驳回结果 → PlanAgent 重新汇总
        return "plan"

    if phase == OrchestrationPhase.RESULT_REVIEW:
        # guard 刚完成结果审核
        max_retries = state.get("max_review_retries", 3)
        if state.get("result_approved") or state.get("review_retries", 0) >= max_retries:
            return "__end__"
        return "plan"

    if phase == OrchestrationPhase.COMPLETED:
        return "__end__"

    return "__end__"
