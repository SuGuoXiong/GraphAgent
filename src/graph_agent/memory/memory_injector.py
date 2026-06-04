"""记忆格式化注入器 —— 将记忆数据转换为上下文消息。

v2 更新：新增 format_profile_v2() / format_memories_v2() 适配新 MemoryRecord 格式。
"""

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
        """将用户画像格式化为 Layer 2 上下文消息（旧 API）。"""
        if not profile.preferences:
            return None
        return self.format_profile_v2([
            {"category": cat, "content": "\n".join(i.content for i in items)}
            for cat, items in profile.preferences.items()
        ])

    def format_memories(self, results: list[SearchResult]) -> MessageBlock | None:
        """将主题记忆检索结果格式化为 Layer 3 上下文消息（旧 API）。"""
        if not results:
            return None
        memory_list = [
            {
                "id": r.slug,
                "type": "general_knowledge",
                "content": f"任务: {r.entry.task}\n方案: {r.entry.approach}\n经验: {r.entry.lessons}",
                "created_at": r.entry.date,
            }
            for r in results
        ]
        return self.format_memories_v2(memory_list)

    # ── v2 格式化方法（新增） ─────────────────────────────

    @classmethod
    def format_profile_v2(cls, profile_list: list[dict] | None) -> MessageBlock | None:
        """将用户偏好列表格式化为 Layer 2 上下文消息（v2）。

        Args:
            profile_list: load_for_context() 返回的 user_preferences_list
        """
        if not profile_list:
            return None

        lines = [
            "[系统记忆] 以下是该用户的长期偏好，"
            "请在意图分析和方案审核时遵守这些偏好：",
            "",
        ]

        char_count = 0
        for item in profile_list:
            content = item.get("content", "")
            if char_count + len(content) > cls.MAX_PROFILE_CHARS:
                lines.append("(...更多偏好已省略)")
                break
            lines.append(f"- {content}")
            char_count += len(content)

        return MessageBlock(
            role="system",
            content="\n".join(lines),
            name="MemoryProfile",
            message_type=MessageType.SYSTEM_MEMORY_PROFILE.value,
            message_id="",
            metadata={"source": "user_preferences_v2"},
        )

    @classmethod
    def format_memories_v2(cls, memory_list: list[dict] | None) -> MessageBlock | None:
        """将记忆列表格式化为 Layer 3 上下文消息（v2）。

        Args:
            memory_list: search() 或 load_for_context() 返回的记忆 dict 列表
        """
        if not memory_list:
            return None

        lines = [
            "[系统记忆] 以下是你过去处理类似任务时的经验记录，"
            "请参考其中的成功方案和用户反馈来优化本次任务计划：",
            "",
        ]

        type_labels = {
            "user_preference": "用户偏好",
            "arch_design": "架构设计",
            "project_rule": "项目规范",
            "design_pattern": "编码建模",
            "bug_pitfall": "Bug踩坑",
            "general_knowledge": "通用常识",
        }

        char_count = 0
        for i, item in enumerate(memory_list, 1):
            type_label = type_labels.get(item.get("type", ""), item.get("type", ""))
            header = f"[{type_label}记忆 - ID:{item.get('id', '')}]"
            content = item.get("content", "")

            entry_text = f"### {header}\n{content}\n\n"
            if char_count + len(entry_text) > cls.MAX_MEMORY_CHARS:
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
            metadata={"source": "memory_v2"},
        )
