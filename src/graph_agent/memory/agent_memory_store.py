"""Agent 记忆检索 —— Layer 3 扩展点。

基于关键词匹配 + 时间衰减的主题记忆检索，
为 PlanAgent 提供历史相似任务的经验参考。
通过 MemoryManager 统一管理存储、检索和衰减。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_agent.message import MessageBlock
    from graph_agent.memory.memory_manager import MemoryManager


class AgentMemoryStore:
    """Agent 记忆存储 —— 委托给 MemoryManager 实现。

    作为 PlanContextBuilder 的 memory_store 参数注入，
    提供 to_context_message() 用于 Layer 3 上下文注入。
    """

    def __init__(self, manager: "MemoryManager | None" = None):
        self._manager = manager

    def set_manager(self, manager: "MemoryManager") -> None:
        self._manager = manager

    def search_similar_tasks(self, intent: str, top_k: int = 3) -> list[dict]:
        """检索与当前意图相似的历史任务。"""
        if self._manager is None:
            return []
        return self._manager.search(intent, top_k)

    def to_context_message(self, intent: str, top_k: int = 3) -> "MessageBlock | None":
        """将记忆检索结果格式化为上下文消息（Layer 3 注入时调用）。"""
        results = self.search_similar_tasks(intent, top_k)
        if not results or self._manager is None:
            return None
        return self._manager.to_memory_message(results)
