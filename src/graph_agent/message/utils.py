import uuid
from typing import Any, Literal

from graph_agent.message.base import MessageBlock, ContentBlock
from graph_agent.message.message_type import MessageType


def generate_message_id() -> str:
    """生成全局唯一的消息 ID。"""
    return uuid.uuid4().hex


def create_user_message(
    content: str,
    message_type: MessageType = MessageType.USER_INPUT,
    name: str = "",
    metadata: dict[str, Any] | None = None,
) -> MessageBlock:
    """创建用户侧消息。"""
    return MessageBlock(
        role="user",
        content=content,
        name=name,
        message_type=message_type.value,
        message_id=generate_message_id(),
        metadata=metadata or {},
    )


def create_assistant_message(
    content: str | list[ContentBlock],
    message_type: MessageType = MessageType.AGENT_RESPONSE,
    name: str = "",
    metadata: dict[str, Any] | None = None,
) -> MessageBlock:
    """创建 Agent 侧消息。"""
    return MessageBlock(
        role="assistant",
        content=content,
        name=name,
        message_type=message_type.value,
        message_id=generate_message_id(),
        metadata=metadata or {},
    )


def create_tool_result_message(
    tool_id: str,
    tool_name: str,
    output: str,
    status: Literal["success", "failure"] = "success",
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> MessageBlock:
    """创建工具结果消息。"""
    from graph_agent.message.base import ToolResultBlock

    result_block = ToolResultBlock(
        tool_id=tool_id,
        tool_name=tool_name,
        output=output,
        status=status,
        error_message=error_message,
    )
    return MessageBlock(
        role="tool",
        content=[ContentBlock(block_type="tool_result", tool_result=result_block)],
        name=tool_name,
        message_type=MessageType.TOOL_RESULT.value,
        message_id=generate_message_id(),
        metadata=metadata or {},
    )


def create_system_message(
    content: str,
    message_type: MessageType = MessageType.SYSTEM_PROMPT,
    metadata: dict[str, Any] | None = None,
) -> MessageBlock:
    """创建系统侧消息。"""
    return MessageBlock(
        role="system",
        content=content,
        name="",
        message_type=message_type.value,
        message_id=generate_message_id(),
        metadata=metadata or {},
    )


def validate_message(msg: MessageBlock) -> bool:
    """校验 MessageBlock 字段完整性和合法性。"""
    if not isinstance(msg.role, str) or msg.role not in ("user", "assistant", "system", "tool"):
        return False
    if not isinstance(msg.message_id, str) or not msg.message_id:
        return False
    if not isinstance(msg.message_type, str) or not msg.message_type:
        return False
    if msg.content is None:
        return False
    if isinstance(msg.content, str) and not msg.content:
        return False
    if isinstance(msg.content, list) and len(msg.content) == 0:
        return False
    return True


def is_tool_call(msg: MessageBlock) -> bool:
    """判断消息是否包含工具调用请求。"""
    if msg.message_type != MessageType.AGENT_ACTION.value:
        return False
    if isinstance(msg.content, list):
        return any(
            block.block_type == "tool_use" and block.tool_use is not None
            for block in msg.content
        )
    return False


def is_tool_result(msg: MessageBlock) -> bool:
    """判断消息是否为工具执行结果。"""
    return msg.role == "tool" and msg.message_type == MessageType.TOOL_RESULT.value
