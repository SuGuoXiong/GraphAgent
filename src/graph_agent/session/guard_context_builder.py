"""Layer 2 上下文构建器 —— GuardAgent 战略视图。

在 Layer 1（ConversationHistory 全量消息）基础上：
1. 过滤内部编排产物（ContextFilter）
2. 合并当前轮次尚未持久化的 Agent 消息（extra_messages）
3. 排除被驳回的中间产物（rejected proposals/syntheses）
4. 检查 Token 阈值，按需执行普通/高度压缩
5. [未来] 注入用户长期偏好
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from graph_agent.message import MessageBlock
from graph_agent.message.message_type import MessageType
from graph_agent.session.compressor import (
    SessionConfig,
    PriorityCompressor,
    SummaryCompressor,
)
from graph_agent.session.context_filter import is_context_eligible
from graph_agent.session.history import ConversationHistory

if TYPE_CHECKING:
    from graph_agent.llm.base import LLMProvider


def _is_rejected_proposal(msg: MessageBlock) -> bool:
    """检查是否为被驳回的方案/汇总消息。"""
    msg_type = MessageType(msg.message_type)
    if msg_type not in (MessageType.PLAN_PROPOSAL, MessageType.PLAN_RESULT_SYNTHESIS):
        return False
    return msg.metadata.get("approved") is False if msg.metadata else False


class GuardContextBuilder:
    """Layer 2 上下文构建器 —— GuardAgent 战略视图。

    为 GuardAgent 的意图分析、方案审核、结果验收三个阶段
    提供经过滤和压缩的战略级上下文。
    """

    def __init__(
        self,
        config: SessionConfig,
        preference_store=None,  # UserPreferenceStore | None —— 扩展点
    ):
        self._config = config
        self._priority_compressor = PriorityCompressor(config)
        self._summary_compressor = SummaryCompressor(config)
        self._preference_store = preference_store

    def build(
        self,
        history: ConversationHistory,
        llm_provider: LLMProvider | None = None,
        user_id: str | None = None,
        extra_messages: list[MessageBlock] | None = None,
    ) -> list[MessageBlock]:
        """构建 GuardAgent 战略视图上下文。

        Args:
            history: 会话的完整对话历史（Layer 1）
            llm_provider: LLM 提供商（高度压缩时需要）
            user_id: 用户标识（未来用于偏好检索）
            extra_messages: 当前轮次已在 state 中但尚未持久化到 history
                           的 Agent 消息

        Returns:
            经过滤和压缩后的消息列表
        """
        # 1. 从 history 中获取所有消息
        all_messages = list(history.messages)

        # 2. 合并 extra_messages（当前轮次新产生的消息）
        if extra_messages:
            all_messages.extend(extra_messages)

        # 3. 过滤：排除内部编排产物
        filtered = [
            m for m in all_messages
            if is_context_eligible(MessageType(m.message_type))
        ]

        # 4. 过滤：排除被驳回的中间产物
        filtered = [m for m in filtered if not _is_rejected_proposal(m)]

        # 5. 检查 Token 阈值，按需压缩
        # 使用临时 history 来复用现有的压缩器
        temp_history = ConversationHistory(
            session_id=history.session_id,
            messages=filtered,
            turn_count=history.turn_count,
        )

        token_count = temp_history.estimate_tokens()
        if token_count > self._config.normal_threshold_tokens:
            filtered = self._priority_compressor.compress(temp_history)

        temp_history.messages = filtered
        token_count = temp_history.estimate_tokens()
        if token_count > self._config.aggressive_threshold_tokens and llm_provider:
            filtered = self._summary_compressor.compress(temp_history, llm_provider)

        # 6. [未来] 注入用户长期偏好
        if self._preference_store and user_id:
            pref_msg = self._preference_store.to_context_message(user_id)
            if pref_msg:
                filtered.insert(0, pref_msg)

        return filtered
