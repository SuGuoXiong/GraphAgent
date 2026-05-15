"""GraphAgent 消息模块。

提供统一的 MessageBlock 格式及其相关的工具函数和转换层，
用于屏蔽底层 LangChain 多种消息类型的差异。
"""

from graph_agent.message.base import (
    MessageBlock,
    ContentBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from graph_agent.message.message_type import MessageType
from graph_agent.message.utils import (
    create_assistant_message,
    create_system_message,
    create_tool_result_message,
    create_user_message,
    generate_message_id,
    is_tool_call,
    is_tool_result,
    validate_message,
)
from graph_agent.message.convert import (
    agent_message_to_langchain,
    agent_messages_to_langchain,
    langchain_to_agent_message,
    langchain_to_agent_messages,
)

__all__ = [
    # 核心数据结构
    "MessageBlock",
    "ContentBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    # 消息类型枚举
    "MessageType",
    # 工厂函数
    "create_user_message",
    "create_assistant_message",
    "create_tool_result_message",
    "create_system_message",
    # 工具函数
    "generate_message_id",
    "validate_message",
    "is_tool_call",
    "is_tool_result",
    # 转换函数
    "agent_message_to_langchain",
    "langchain_to_agent_message",
    "agent_messages_to_langchain",
    "langchain_to_agent_messages",
]
