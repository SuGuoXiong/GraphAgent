"""记忆管理器 —— 核心编排层。

协调五个子系统：存储、提取、检索、注入、衰减。

三层触发：
    1. 实时提取 (extract_async) — execute_turn 返回后异步执行
    2. 会话结束合并 (consolidate) — 下一个 send_message 到达时同步执行
    3. 新会话加载 (load) — create_session / load_session 时同步执行
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from graph_agent.memory.memory_store import MemoryStore
from graph_agent.memory.memory_extractor import MemoryExtractor
from graph_agent.memory.memory_retriever import MemoryRetriever
from graph_agent.memory.memory_injector import MemoryInjector
from graph_agent.memory.memory_types import (
    UserProfile,
    UserPreferenceItem,
    TopicMemory,
    TopicMemoryEntry,
    SearchResult,
    _now,
)

if TYPE_CHECKING:
    from graph_agent.message import MessageBlock

logger = logging.getLogger(__name__)

# 单 topic 超过此数量触发 LLM 合并
_MAX_ENTRIES_PER_TOPIC = 10
# 记忆过期天数
_EXPIRY_DAYS = 90


class MemoryManager:
    """记忆系统统一入口。

    使用方式:
        mgr = MemoryManager()
        user_prefs, memory_index = mgr.load()
        # ... 编排执行 ...
        memory_refs = mgr.search(intent)
        mgr.extract_async(session_id, messages, task_summary)
        # ... 下一个 send_message 时 ...
        mgr.consolidate(session_id)
    """

    def __init__(self, store: MemoryStore | None = None):
        self._store = store or MemoryStore()
        self._extractor = MemoryExtractor(self._store)
        self._retriever = MemoryRetriever(self._store)
        self._injector = MemoryInjector()

        # 暂存区: {session_id: {"profile": [...], "topic": {...}}}
        self._pending: dict[str, dict] = {}

    # ── Layer 1: 加载 (会话创建时调用) ─────────────────

    def load(self) -> tuple[dict | None, list[dict]]:
        """加载用户画像和主题记忆索引。

        Returns:
            (user_preferences_dict, memory_index_list)
        """
        profile = self._store.load_profile()
        index = self._store.load_index()

        user_prefs = self._profile_to_dict(profile) if profile.preferences else None

        memory_index_list = [
            {
                "slug": t.slug,
                "title": t.title,
                "keywords": t.keywords,
                "summary": t.summary,
            }
            for t in index.topics
        ] if index.topics else []

        return user_prefs, memory_index_list

    # ── Layer 2: 检索 (GuardAgent 意图分析后调用) ──────

    def search(self, intent: str, top_k: int = 5) -> list[dict]:
        """检索与当前意图相关的主题记忆。

        Returns:
            匹配的完整主题记忆条目列表 (dict 格式，可直接注入)
        """
        results = self._retriever.search(intent, top_k)
        return [
            {
                "slug": r.slug,
                "title": r.title,
                "score": r.score,
                "entry": {
                    "title": r.entry.title,
                    "task": r.entry.task,
                    "approach": r.entry.approach,
                    "lessons": r.entry.lessons,
                    "user_feedback": r.entry.user_feedback,
                    "user_adjustment": r.entry.user_adjustment,
                    "date": r.entry.date,
                },
            }
            for r in results
        ]

    # ── Layer 3: 提取 (execute_turn 返回后异步调用) ────

    def extract_async(
        self,
        session_id: str,
        messages: list,
        task_summary: str = "",
        user_feedback: str = "",
    ) -> None:
        """提交异步记忆提取任务（不阻塞当前请求）。

        仅将任务放入事件循环，不等待完成。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 没有事件循环，跳过

        loop.create_task(self._extract_and_stage(
            session_id, messages, task_summary, user_feedback,
        ))

    async def _extract_and_stage(
        self,
        session_id: str,
        messages: list,
        task_summary: str,
        user_feedback: str,
    ) -> None:
        """在后台执行提取并将结果写入暂存区。"""
        try:
            result = await self._extractor.extract(
                messages, task_summary, user_feedback,
            )
            self._pending[session_id] = result
            logger.debug(f"记忆提取完成 session={session_id[:12]}...")
        except Exception as e:
            logger.warning(f"记忆提取失败 session={session_id[:12]}...: {e}")

    # ── Layer 4: 合并 (下一次 send_message 时同步调用) ──

    def consolidate(self, session_id: str) -> None:
        """将暂存区中的增量记忆合并到正式存储。

        执行去重、冲突解决、LLM 合并（如需要）。
        """
        pending = self._pending.pop(session_id, None)
        if not pending:
            return

        try:
            # 合并用户画像
            profile_increments = pending.get("profile_increments", [])
            if profile_increments:
                self._merge_profile(profile_increments)

            # 合并主题记忆
            topic_data = pending.get("topic_memory")
            if topic_data:
                self._merge_topic(topic_data)
        except Exception as e:
            logger.warning(f"记忆合并失败: {e}")

    def _merge_profile(self, increments: list[dict]) -> None:
        """将用户画像增量合并到已有画像。

        冲突策略: 同一维度的偏好以最新为准。
        """
        profile = self._store.load_profile()

        for inc in increments:
            category = inc.get("category", "其他")
            content = inc.get("content", "")
            if not content:
                continue

            if category not in profile.preferences:
                profile.preferences[category] = []

            # 检查是否与已有条目语义重复（简单包含判断）
            exists = False
            for item in profile.preferences[category]:
                if content in item.content or item.content in content:
                    # 更新为最新表述
                    item.content = content
                    item.updated_at = _now()
                    item.stale = False
                    exists = True
                    break

            if not exists:
                profile.preferences[category].append(
                    UserPreferenceItem(category=category, content=content)
                )

        # 标记超过 90 天未更新的条为可能过时
        self._mark_stale_preferences(profile)

        self._store.save_profile(profile)

    def _merge_topic(self, data: dict) -> None:
        """将主题记忆增量合并到已有主题存储。

        策略:
        1. 按 topic_slug 查找已有主题
        2. 相似条目合并，否则追加
        3. 超量时触发合并
        """
        slug = data.get("topic_slug", "general")
        title = data.get("topic_title", slug)

        topic = self._store.load_topic(slug)
        if topic is None:
            topic = TopicMemory(
                slug=slug,
                title=title,
                keywords=data.get("keywords", []),
                summary=data.get("title", ""),
                created_at=_now(),
            )

        # 更新元数据
        topic.title = title
        existing_keywords = set(topic.keywords)
        existing_keywords.update(data.get("keywords", []))
        topic.keywords = list(existing_keywords)

        # 检查重复（标题相似度）
        new_title = data.get("title", "")
        duplicate = False
        for entry in topic.entries:
            if _title_similarity(new_title, entry.title) > 0.8:
                # 更新已有条目
                entry.task = data.get("task", entry.task)
                entry.approach = data.get("approach", entry.approach)
                entry.lessons = data.get("lessons", entry.lessons)
                entry.date = _now()
                duplicate = True
                break

        if not duplicate:
            topic.entries.insert(0, TopicMemoryEntry(
                title=new_title,
                task=data.get("task", ""),
                approach=data.get("approach", ""),
                lessons=data.get("lessons", ""),
                user_feedback="",
                date=_now(),
            ))

        # 超量合并（简单截断 + 保留最新 N 条）
        if len(topic.entries) > _MAX_ENTRIES_PER_TOPIC:
            # 保留最新的 MAX_ENTRIES_PER_TOPIC 条
            topic.entries = topic.entries[:_MAX_ENTRIES_PER_TOPIC]

        # 清理过期条目
        topic.entries = [
            e for e in topic.entries
            if _days_since_str(e.date) < _EXPIRY_DAYS
        ]

        topic.updated_at = _now()
        topic.last_accessed_at = _now()
        self._store.save_topic(topic)
        self._store.upsert_index_entry(topic)

    def _mark_stale_preferences(self, profile: UserProfile) -> None:
        """标记超过 90 天未更新的偏好为可能过时。"""
        for items in profile.preferences.values():
            for item in items:
                if _days_since_str(item.updated_at) > _EXPIRY_DAYS:
                    item.stale = True
                    item.confidence = 0.5

    def _profile_to_dict(self, profile: UserProfile) -> dict:
        """UserProfile → 简化 dict（供 state.user_preferences 使用）。"""
        result: dict[str, list[dict]] = {}
        for category, items in profile.preferences.items():
            result[category] = [
                {"content": i.content, "stale": i.stale, "confidence": i.confidence}
                for i in items
            ]
        return result

    # ── Context Builder 集成接口 ───────────────────────

    def to_profile_message(self, profile_dict: dict | None) -> "MessageBlock | None":
        """将 user_preferences dict 转为上下文消息（Layer 2 注入）。"""
        if not profile_dict:
            return None
        # 重建 UserProfile 用于注入器
        profile = UserProfile()
        for category, items in profile_dict.items():
            profile.preferences[category] = [
                UserPreferenceItem(
                    category=category,
                    content=i.get("content", ""),
                    stale=i.get("stale", False),
                    confidence=i.get("confidence", 1.0),
                )
                for i in items
            ]
        return self._injector.format_profile(profile)

    def to_memory_message(self, memory_list: list[dict] | None) -> "MessageBlock | None":
        """将 memory_refs list 转为上下文消息（Layer 3 注入）。"""
        if not memory_list:
            return None
        results = [
            SearchResult(
                slug=m.get("slug", ""),
                title=m.get("title", ""),
                entry=TopicMemoryEntry(
                    title=m.get("entry", {}).get("title", ""),
                    task=m.get("entry", {}).get("task", ""),
                    approach=m.get("entry", {}).get("approach", ""),
                    lessons=m.get("entry", {}).get("lessons", ""),
                    user_feedback=m.get("entry", {}).get("user_feedback", ""),
                    user_adjustment=m.get("entry", {}).get("user_adjustment", ""),
                    date=m.get("entry", {}).get("date", ""),
                ),
                score=m.get("score", 0),
            )
            for m in memory_list
        ]
        return self._injector.format_memories(results)


# ── Helpers ────────────────────────────────────────────────

def _title_similarity(a: str, b: str) -> float:
    """计算两个标题的简单相似度（基于公共字符）。"""
    if not a or not b:
        return 0.0
    a_set = set(a)
    b_set = set(b)
    intersection = len(a_set & b_set)
    union = len(a_set | b_set)
    return intersection / union if union > 0 else 0.0


def _days_since_str(date_str: str) -> float:
    """计算日期字符串距今的天数。"""
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - d).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return 999.0
