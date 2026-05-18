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
from graph_agent.tracer import get_tracer

_prompt_loader = PromptLoader()


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
        conversation_context=_format_context(state.get("messages", [])),
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
        conversation_context=_format_context(state.get("messages", [])),
        task_plan=str(task_plan),
        intent=state.get("intent", ""),
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
        conversation_context=_format_context(state.get("messages", [])),
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
