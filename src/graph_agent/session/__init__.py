"""多轮对话会话管理模块。

提供对话历史的存储、持久化、压缩等能力：
- ConversationHistory: 内存中的对话历史管理
- ConversationPersistence: JSON 磁盘序列化
- PriorityCompressor / SummaryCompressor: 两级上下文压缩
- estimate_tokens: Token 估算工具
- GuardContextBuilder: Layer 2 上下文构建器（GuardAgent 战略视图）
- PlanContextBuilder: Layer 3 上下文构建器（PlanAgent 战术视图）
- SubAgentContextBuilder: Layer 4 上下文构建器（SubAgent 执行视图）
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
from graph_agent.session.guard_context_builder import GuardContextBuilder
from graph_agent.session.plan_context_builder import PlanContextBuilder
from graph_agent.session.subagent_context_builder import SubAgentContextBuilder

__all__ = [
    "ConversationHistory",
    "ConversationPersistence",
    "SessionConfig",
    "PriorityCompressor",
    "SummaryCompressor",
    "GuardContextBuilder",
    "PlanContextBuilder",
    "SubAgentContextBuilder",
    "is_context_eligible",
    "estimate_tokens",
    "sanitize_text",
]
