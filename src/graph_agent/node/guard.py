"""GuardAgent 节点——意图识别、方案审核、结果验收。

GuardAgent 不可见、不可调用任何 Tool，仅依赖 LLM 语义推理。
"""

import json

from graph_agent.orchestration.state import OrchestrationState, OrchestrationPhase
from graph_agent.orchestration.prompt_loader import PromptLoader
from graph_agent.message import (
    MessageBlock, ContentBlock,
    create_assistant_message, create_system_message,
    generate_message_id,
    agent_message_to_langchain,
)
from graph_agent.message.message_type import MessageType

_prompt_loader = PromptLoader()


def _call_llm(system_prompt: str, user_text: str,
              state: OrchestrationState,
              name: str = "GuardAgent",
              message_type: str = "agent_response") -> MessageBlock:
    """调用 LLM 并返回 MessageBlock 格式的响应。"""
    from graph_agent.llm import LLMFactory

    provider = LLMFactory.create_from_env()
    llm = provider.get_chat_model()

    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_text)]
    response = llm.invoke(messages)
    text = response.content if hasattr(response, 'content') else str(response)

    return create_assistant_message(
        content=text,
        message_type=MessageType.AGENT_RESPONSE,
        name=name,
        metadata={"message_type_override": message_type},
    )


def _format_context(messages: list) -> str:
    """格式化对话上下文为纯文本。"""
    if not messages:
        return "(无历史消息)"
    parts = []
    for m in messages[-10:]:
        content = m.content if isinstance(m.content, str) else str(m.content)
        parts.append(f"[{m.type if hasattr(m, 'type') else '?'}]: {content[:500]}")
    return "\n".join(parts)


def _parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


def _analyze_intent(state: OrchestrationState) -> dict:
    """意图识别阶段。"""
    system_prompt = _prompt_loader.load_with_context(
        "guard", "intent_analysis",
        conversation_context=_format_context(state.get("messages", [])),
    )
    user_text = state.get("messages", [])[-1].content if state.get("messages") else ""
    msg = _call_llm(system_prompt, user_text, state,
                    name="GuardAgent", message_type="guard_intent")
    result = _parse_json_response(msg.content if isinstance(msg.content, str) else "")

    return {
        "phase": OrchestrationPhase.PLAN_GENERATION,
        "intent": result.get("intent", user_text),
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def _review_plan(state: OrchestrationState) -> dict:
    """方案审核阶段。"""
    task_plan = state.get("task_plan")
    system_prompt = _prompt_loader.load_with_context(
        "guard", "plan_review",
        conversation_context=_format_context(state.get("messages", [])),
        task_plan=str(task_plan),
        intent=state.get("intent", ""),
    )
    user_text = f"请审核以下任务计划:\n{task_plan}"
    msg = _call_llm(system_prompt, user_text, state,
                    name="GuardAgent", message_type="plan_review")
    result = _parse_json_response(msg.content if isinstance(msg.content, str) else "")

    approved = result.get("approved", True)
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
    sub_results = state.get("sub_results", {})
    final_result = "\n".join(f"{k}: {v}" for k, v in sub_results.items())

    system_prompt = _prompt_loader.load_with_context(
        "guard", "result_review",
        conversation_context=_format_context(state.get("messages", [])),
        final_result=final_result,
        intent=state.get("intent", ""),
    )
    user_text = f"请验收以下最终结果:\n{final_result}"
    msg = _call_llm(system_prompt, user_text, state,
                    name="GuardAgent", message_type="result_review")
    result = _parse_json_response(msg.content if isinstance(msg.content, str) else "")

    approved = result.get("approved", True)
    return {
        "phase": OrchestrationPhase.COMPLETED if approved else OrchestrationPhase.RESULT_SYNTHESIS,
        "result_approved": approved,
        "guard_feedback": result.get("feedback", ""),
        "review_retries": state.get("review_retries", 0) + (0 if approved else 1),
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def guard_node(state: OrchestrationState) -> dict:
    """GuardAgent 节点：根据当前 phase 执行对应职责。"""
    phase = state.get("phase", OrchestrationPhase.INTENT_ANALYSIS)

    if phase == OrchestrationPhase.INTENT_ANALYSIS:
        return _analyze_intent(state)
    elif phase == OrchestrationPhase.PLAN_REVIEW:
        return _review_plan(state)
    elif phase == OrchestrationPhase.RESULT_REVIEW:
        return _review_result(state)
    return {}


def guard_router(state: OrchestrationState) -> str:
    """GuardAgent 的路由决策。

    注意：此时 state 已包含 guard_node 的返回值，
    因此 phase 反映的是 guard_node 执行*后*的状态。
    """
    phase = state.get("phase", OrchestrationPhase.INTENT_ANALYSIS)

    if phase == OrchestrationPhase.PLAN_GENERATION:
        # guard 刚完成意图分析 → PlanAgent 制定方案
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
