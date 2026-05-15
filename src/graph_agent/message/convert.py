"""MessageBlock 与 LangChain BaseMessage 之间的双向转换。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from graph_agent.message.base import MessageBlock, ContentBlock, ToolResultBlock, ToolUseBlock
from graph_agent.message.message_type import MessageType
from graph_agent.message.utils import generate_message_id


def _extract_text_from_blocks(blocks: list[ContentBlock]) -> str:
    """从 ContentBlock 列表中提取所有文本和思考内容，拼接为纯文本。"""
    parts: list[str] = []
    for block in blocks:
        if block.block_type == "text" and block.text:
            parts.append(block.text)
        elif block.block_type == "thinking" and block.thinking:
            parts.append(block.thinking)
    return "\n".join(parts)


def _extract_tool_use_blocks(blocks: list[ContentBlock]) -> list[ToolUseBlock]:
    """从 ContentBlock 列表中提取所有工具调用块。"""
    return [
        block.tool_use
        for block in blocks
        if block.block_type == "tool_use" and block.tool_use is not None
    ]


def _build_content_blocks_from_aimessage(
    text_content: str, tool_calls: list[dict[str, Any]]
) -> list[ContentBlock]:
    """根据 AIMessage 的文本和 tool_calls 构建 ContentBlock 列表。"""
    blocks: list[ContentBlock] = []
    if text_content:
        blocks.append(ContentBlock(block_type="text", text=text_content))
    for tc in tool_calls:
        blocks.append(
            ContentBlock(
                block_type="tool_use",
                tool_use=ToolUseBlock(
                    tool_id=tc.get("id", ""),
                    tool_name=tc.get("name", ""),
                    input_args=tc.get("args", {}),
                ),
            )
        )
    return blocks


def agent_message_to_langchain(msg: MessageBlock) -> BaseMessage:
    """将单个 MessageBlock 转换为 LangChain BaseMessage 子类实例。"""
    role = msg.role

    if role == "user":
        content = msg.content if isinstance(msg.content, str) else _extract_text_from_blocks(msg.content)
        return HumanMessage(content=content, name=msg.name or None)

    if role == "assistant":
        if isinstance(msg.content, list):
            text = _extract_text_from_blocks(msg.content)
            tool_uses = _extract_tool_use_blocks(msg.content)
            additional_kwargs: dict[str, Any] = {}
            if tool_uses:
                additional_kwargs["tool_calls"] = [
                    {
                        "id": tu.tool_id,
                        "name": tu.tool_name,
                        "args": tu.input_args,
                    }
                    for tu in tool_uses
                ]
            return AIMessage(
                content=text or "",
                additional_kwargs=additional_kwargs,
                name=msg.name or None,
            )
        # content is str
        return AIMessage(content=msg.content, name=msg.name or None)

    if role == "system":
        content = msg.content if isinstance(msg.content, str) else _extract_text_from_blocks(msg.content)
        return SystemMessage(content=content)

    if role == "tool":
        if isinstance(msg.content, list):
            result_block: ToolResultBlock | None = None
            for block in msg.content:
                if block.block_type == "tool_result" and block.tool_result is not None:
                    result_block = block.tool_result
                    break
            if result_block:
                return ToolMessage(
                    content=result_block.output,
                    tool_call_id=result_block.tool_id,
                    name=result_block.tool_name,
                )
            return ToolMessage(
                content=_extract_text_from_blocks(msg.content),
                tool_call_id=msg.metadata.get("tool_call_id", ""),
                name=msg.name or None,
            )
        return ToolMessage(
            content=msg.content,
            tool_call_id=msg.metadata.get("tool_call_id", ""),
            name=msg.name or None,
        )

    # fallback
    content = msg.content if isinstance(msg.content, str) else _extract_text_from_blocks(msg.content)
    return AIMessage(content=content, name=msg.name or None)


def langchain_to_agent_message(msg: BaseMessage) -> MessageBlock:
    """将单个 LangChain BaseMessage 转换为 MessageBlock。"""
    msg_id = generate_message_id()
    metadata: dict[str, Any] = {"langchain_type": type(msg).__name__}

    if isinstance(msg, HumanMessage):
        return MessageBlock(
            role="user",
            content=msg.content if isinstance(msg.content, str) else str(msg.content),
            name=msg.name or "",
            message_type=MessageType.USER_INPUT.value,
            message_id=msg_id,
            metadata=metadata,
        )

    if isinstance(msg, AIMessage):
        tool_calls = _extract_langchain_tool_calls(msg)
        content = msg.content if isinstance(msg.content, str) else str(msg.content)

        if tool_calls:
            blocks = _build_content_blocks_from_aimessage(content, tool_calls)
            return MessageBlock(
                role="assistant",
                content=blocks,
                name=msg.name or "",
                message_type=MessageType.AGENT_ACTION.value,
                message_id=msg_id,
                metadata=metadata,
            )

        return MessageBlock(
            role="assistant",
            content=content,
            name=msg.name or "",
            message_type=MessageType.AGENT_RESPONSE.value,
            message_id=msg_id,
            metadata=metadata,
        )

    if isinstance(msg, SystemMessage):
        return MessageBlock(
            role="system",
            content=msg.content if isinstance(msg.content, str) else str(msg.content),
            name="",
            message_type=MessageType.SYSTEM_PROMPT.value,
            message_id=msg_id,
            metadata=metadata,
        )

    if isinstance(msg, ToolMessage):
        tool_name = msg.name or ""
        result_block = ToolResultBlock(
            tool_id=msg.tool_call_id,
            tool_name=tool_name,
            output=str(msg.content),
            status="success",
        )
        return MessageBlock(
            role="tool",
            content=[ContentBlock(block_type="tool_result", tool_result=result_block)],
            name=tool_name,
            message_type=MessageType.TOOL_RESULT.value,
            message_id=msg_id,
            metadata={
                **metadata,
                "tool_call_id": msg.tool_call_id,
            },
        )

    # 不可识别类型的降级策略
    return MessageBlock(
        role="system",
        content=msg.content if isinstance(msg.content, str) else str(msg.content),
        name="",
        message_type=MessageType.SYSTEM_NOTIFICATION.value,
        message_id=msg_id,
        metadata={
            **metadata,
            "original_message_type": type(msg).__name__,
            "original_content": str(msg.content),
        },
    )


def _extract_langchain_tool_calls(msg: AIMessage) -> list[dict[str, Any]]:
    """从 AIMessage 中提取 tool_calls 为统一格式列表。"""
    tool_calls: list[dict[str, Any]] = []

    # LangChain 标准 tool_calls 属性
    if hasattr(msg, "tool_calls") and msg.tool_calls:
        tool_calls = list(msg.tool_calls)
    # additional_kwargs 中的 tool_calls（部分模型使用此格式）
    elif msg.additional_kwargs.get("tool_calls"):
        raw_calls = msg.additional_kwargs["tool_calls"]
        for tc in raw_calls:
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": tc.get("function", {}).get("name", tc.get("name", "")),
                "args": tc.get("function", {}).get("arguments", tc.get("args", {})),
            })
            # 如果 args 是 JSON 字符串，在使用时由调用方自行解析

    return tool_calls


def agent_messages_to_langchain(msgs: list[MessageBlock]) -> list[BaseMessage]:
    """批量将 MessageBlock 列表转换为 LangChain BaseMessage 列表。"""
    return [agent_message_to_langchain(m) for m in msgs]


def langchain_to_agent_messages(msgs: list[BaseMessage]) -> list[MessageBlock]:
    """批量将 LangChain BaseMessage 列表转换为 MessageBlock 列表。"""
    return [langchain_to_agent_message(m) for m in msgs]
