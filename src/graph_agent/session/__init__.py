"""多轮对话会话管理模块。

提供对话历史的存储、持久化、压缩等能力：
- ConversationHistory: 内存中的对话历史管理
- ConversationPersistence: JSON 磁盘序列化
- PriorityCompressor / SummaryCompressor: 两级上下文压缩
- estimate_tokens: Token 估算工具
"""

from graph_agent.session.token_counter import estimate_tokens
from graph_agent.session.history import ConversationHistory
from graph_agent.session.persistence import ConversationPersistence, sanitize_text
from graph_agent.session.context_filter import is_context_eligible
from graph_agent.session.compressor import (
    SessionConfig,
    PriorityCompressor,
    SummaryCompressor,
)

__all__ = [
    "ConversationHistory",
    "ConversationPersistence",
    "SessionConfig",
    "PriorityCompressor",
    "SummaryCompressor",
    "is_context_eligible",
    "estimate_tokens",
    "sanitize_text",
]
