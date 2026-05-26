"""记忆格式化注入器 —— 将记忆数据转换为上下文消息。"""

from __future__ import annotations

from graph_agent.memory.memory_types import (
    UserProfile,
    UserPreferenceItem,
    SearchResult,
)
from graph_agent.message import MessageBlock
from graph_agent.message.message_type import MessageType


class MemoryInjector:
    """将记忆数据格式化并转换为 MessageBlock，供 context builder 注入。"""

    # 上下文预算（tokens，近似按 1 token ≈ 2 字符估算）
    MAX_PROFILE_CHARS = 1000    # ~500 tokens
    MAX_MEMORY_CHARS = 3000     # ~1500 tokens

    def format_profile(self, profile: UserProfile) -> MessageBlock | None:
        """将用户画像格式化为 Layer 2 上下文消息。

        在对话历史之前注入，告知 GuardAgent 用户长期偏好。
        如果画像为空，返回 None。
        """
        if not profile.preferences:
            return None

        lines = [
            "[系统记忆] 以下是该用户的长期偏好，"
            "请在意图分析和方案审核时遵守这些偏好：",
            "",
        ]

        char_count = 0
        for category, items in profile.preferences.items():
            if char_count > self.MAX_PROFILE_CHARS:
                break
            lines.append(f"## {category}")
            for item in items:
                stale_marker = "⚠️ [可能已过时] " if item.stale else ""
                line = f"- {stale_marker}{item.content}"
                if char_count + len(line) > self.MAX_PROFILE_CHARS:
                    lines.append("- (...更多偏好已省略)")
                    break
                lines.append(line)
                char_count += len(line)
            lines.append("")

        return MessageBlock(
            role="system",
            content="\n".join(lines),
            name="MemoryProfile",
            message_type=MessageType.SYSTEM_MEMORY_PROFILE.value,
            message_id="",
            metadata={"source": "user_profile"},
        )

    def format_memories(self, results: list[SearchResult]) -> MessageBlock | None:
        """将主题记忆检索结果格式化为 Layer 3 上下文消息。

        在 GuardAgent 意图分析之后注入，为 PlanAgent 提供历史经验参考。
        若无匹配结果，返回 None。
        """
        if not results:
            return None

        lines = [
            "[系统记忆] 以下是你过去处理类似任务时的经验记录，"
            "请参考其中的成功方案和用户反馈来优化本次任务计划：",
            "",
        ]

        char_count = 0
        for i, r in enumerate(results, 1):
            entry_header = (
                f"### 经验 {i}: {r.entry.title} "
                f"(相关度: {r.score:.2f}, 主题: {r.title})"
            )
            entry_text = (
                f"{entry_header}\n"
                f"- 日期: {r.entry.date}\n"
                f"- 任务: {r.entry.task}\n"
                f"- 方案: {r.entry.approach}\n"
                f"- 经验: {r.entry.lessons}"
            )
            if r.entry.user_feedback:
                entry_text += f"\n- 用户反馈: {r.entry.user_feedback}"
            if r.entry.user_adjustment:
                entry_text += f"\n- 用户调整: {r.entry.user_adjustment}"
            entry_text += "\n"

            if char_count + len(entry_text) > self.MAX_MEMORY_CHARS:
                lines.append("(...更多历史经验已省略)")
                break

            lines.append(entry_text)
            char_count += len(entry_text)

        return MessageBlock(
            role="system",
            content="\n".join(lines),
            name="MemoryRetriever",
            message_type=MessageType.SYSTEM_MEMORY_TOPIC.value,
            message_id="",
            metadata={"source": "topic_memory"},
        )
