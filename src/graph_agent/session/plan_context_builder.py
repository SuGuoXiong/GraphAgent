"""Layer 3 上下文构建器 —— PlanAgent 战术视图。

在 Layer 2（GuardAgent 战略视图）基础上：
1. 调用 GuardContextBuilder 获取过滤压缩后的消息
2. 追加 GuardAgent 意图分析结果
3. 追加 SubAgent 能力清单
4. [未来] 检索并注入相关历史案例（Agent 记忆检索）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from graph_agent.message import MessageBlock
from graph_agent.message.message_type import MessageType
from graph_agent.session.guard_context_builder import GuardContextBuilder
from graph_agent.session.compressor import SessionConfig
from graph_agent.session.history import ConversationHistory

if TYPE_CHECKING:
    from graph_agent.llm.base import LLMProvider
    from graph_agent.orchestration.subagent import SubAgentRegistry


class PlanContextBuilder:
    """Layer 3 上下文构建器 —— PlanAgent 战术视图。

    为 PlanAgent 的任务分解、方案制定、结果汇总提供战术级上下文，
    包含 GuardAgent 意图分析结果和可用的 SubAgent 清单。
    """

    def __init__(
        self,
        config: SessionConfig,
        guard_context_builder: GuardContextBuilder | None = None,
        memory_store=None,  # AgentMemoryStore | None —— 扩展点
    ):
        self._config = config
        self._guard_context_builder = guard_context_builder or GuardContextBuilder(config)
        self._memory_store = memory_store

    def build(
        self,
        history: ConversationHistory,
        intent_analysis: MessageBlock | None,
        subagent_registry: SubAgentRegistry,
        llm_provider: LLMProvider | None = None,
        user_id: str | None = None,
        extra_messages: list[MessageBlock] | None = None,
    ) -> list[MessageBlock]:
        """构建 PlanAgent 战术视图上下文。

        Args:
            history: 会话的完整对话历史（Layer 1）
            intent_analysis: GuardAgent 意图分析结果消息（可为 None）
            subagent_registry: SubAgent 注册中心，用于生成能力清单
            llm_provider: LLM 提供商
            user_id: 用户标识
            extra_messages: 当前轮次尚未持久化的 Agent 消息

        Returns:
            PlanAgent 专用的上下文消息列表
        """
        # 1. 从 GuardContextBuilder 获取 Layer 2 战略视图
        context = self._guard_context_builder.build(
            history,
            llm_provider=llm_provider,
            user_id=user_id,
            extra_messages=extra_messages,
        )

        # 2. 追加 GuardAgent 意图分析结果
        if intent_analysis is not None:
            context.append(intent_analysis)

        # 3. 追加 SubAgent 能力清单
        catalog_text = subagent_registry.describe_all_for_llm()
        if catalog_text:
            catalog_msg = MessageBlock(
                role="system",
                content=f"## 可用 SubAgent 能力清单\n{catalog_text}",
                name="SubAgentRegistry",
                message_type=MessageType.SYSTEM_NOTIFICATION.value,
                message_id="",
                metadata={"source": "subagent_catalog"},
            )
            context.append(catalog_msg)

        # 4. [未来] 检索并注入相关历史案例
        if self._memory_store and intent_analysis is not None:
            intent_text = (
                intent_analysis.content
                if isinstance(intent_analysis.content, str)
                else str(intent_analysis.content)
            )
            memory_msg = self._memory_store.to_context_message(intent_text)
            if memory_msg:
                context.append(memory_msg)

        return context
