from dataclasses import dataclass, field
from typing import Any, Literal
from datetime import datetime, timezone


@dataclass
class ToolUseBlock:
    tool_id: str
    tool_name: str
    input_args: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_id: str
    tool_name: str
    output: str
    status: Literal["success", "failure"]
    error_message: str | None = None  # 明确允许 None，更规范


@dataclass
class ContentBlock:
    block_type: Literal["text", "tool_use", "tool_result", "thinking"]
    text: str | None = None
    tool_use: ToolUseBlock | None = None
    tool_result: ToolResultBlock | None = None
    thinking: str | None = None


@dataclass
class MessageBlock:
    # 消息来源角色
    role: Literal["user", "assistant", "system", "tool"]

    # 消息内容
    content: str | list[ContentBlock]

    # 对role进一步划分，例如当role为工具时，name为工具id，当role为assistant时，name为agent名称
    name: str

    # 消息类型，用于标识这条消息的种类
    message_type: str

    # message的唯一标识
    message_id: str

    # 扩展字段
    metadata: dict[str, Any] = None

    # 新增：自动生成 UTC 时间戳
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
