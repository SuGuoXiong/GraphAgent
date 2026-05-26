"""上下文过滤 —— 在注入 LLM 前排除内部审核/派发/工具消息。

编排流程每轮产生 8+ 条消息，其中审核类消息（GUARD_PLAN_REVIEW）、
任务派发指令（PLAN_TASK_DISPATCH）和工具调用（TOOL_CALL / TOOL_RESULT）
是内部质量控制信号，对后续对话的语义理解没有贡献。
此模块在注入上下文前过滤掉这些消息。
"""

from graph_agent.message.message_type import MessageType

_EXCLUDED_FROM_CONTEXT: set[MessageType] = {
    MessageType.GUARD_PLAN_REVIEW,
    MessageType.PLAN_TASK_DISPATCH,
    MessageType.TOOL_CALL,
    MessageType.TOOL_RESULT,
}


def is_context_eligible(message_type: MessageType) -> bool:
    """判断消息类型是否应被包含在注入 LLM 的上下文中。"""
    return message_type not in _EXCLUDED_FROM_CONTEXT
