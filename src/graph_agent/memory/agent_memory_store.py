"""Agent 记忆检索 —— Layer 3 扩展点（空壳实现）。

未来在此模块中实现基于向量检索的历史任务案例匹配，
为 PlanAgent 提供相似任务的分解方案参考。

当前空壳实现直接返回空结果，不影响主流程。
"""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_agent.message import MessageBlock
    from graph_agent.orchestration.state import TaskPlan


class AgentMemoryStore(ABC):
    """Agent 记忆存储（抽象基类，当前为空壳实现）。

    使用具体方法而非 @abstractmethod，使得未实现子类化时仍可
    直接实例化，表现为"无记忆"（空壳模式）。

    未来实现方向：
    - 向量化：使用 Embedding 模型将意图和方案编码为向量
    - 检索策略：语义相似度 + 结果质量加权
    - 记忆更新：每轮对话完成后自动记录
    """

    def search_similar_tasks(
        self, intent: str, top_k: int = 3,
    ) -> list[dict]:
        """检索与当前意图相似的历史任务。

        Returns:
            [{"intent": str, "plan_summary": str, "outcome": str, "score": float}, ...]
        """
        return []

    def record_plan_result(
        self, intent: str, plan: TaskPlan, outcome: str,
    ) -> None:
        """记录一次任务分解的结果（用于未来检索）。"""
        pass

    def to_context_message(self, intent: str, top_k: int = 3) -> MessageBlock | None:
        """将记忆检索结果格式化为上下文消息（Layer 3 注入时调用）。"""
        results = self.search_similar_tasks(intent, top_k)
        if not results:
            return None
        from graph_agent.message import MessageBlock
        from graph_agent.message.message_type import MessageType

        lines = ["## 历史参考案例"]
        for i, r in enumerate(results, 1):
            lines.append(
                f"### 案例 {i} (相似度: {r.get('score', 0):.2f})\n"
                f"- 意图: {r.get('intent', '')}\n"
                f"- 方案: {r.get('plan_summary', '')}\n"
                f"- 结果: {r.get('outcome', '')}"
            )
        return MessageBlock(
            role="assistant",
            content="\n\n".join(lines),
            name="MemoryRetriever",
            message_type=MessageType.AGENT_RESPONSE.value,
            message_id="",
            metadata={"source": "agent_memory"},
        )
