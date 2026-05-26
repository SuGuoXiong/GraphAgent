"""记忆系统数据类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class UserPreferenceItem:
    """单条用户偏好。"""
    category: str
    content: str
    confidence: float = 1.0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    stale: bool = False


@dataclass
class UserProfile:
    """用户画像文件（user_profile.md 的内存表示）。"""
    preferences: dict[str, list[UserPreferenceItem]] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now)
    total_extractions: int = 0


@dataclass
class TopicMemoryEntry:
    """单条主题记忆条目。"""
    title: str
    task: str
    approach: str
    lessons: str
    user_feedback: str = ""
    user_adjustment: str = ""
    date: str = field(default_factory=_now)


@dataclass
class TopicMemory:
    """一个主题下的完整记忆文件（topic/*.md 的内存表示）。"""
    slug: str
    title: str
    keywords: list[str] = field(default_factory=list)
    summary: str = ""
    entries: list[TopicMemoryEntry] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_accessed_at: str = field(default_factory=_now)


@dataclass
class TopicIndexEntry:
    """index.json 中单个 topic 的索引条目。"""
    slug: str
    title: str
    path: str
    keywords: list[str] = field(default_factory=list)
    summary: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str = ""
    entry_count: int = 0
    embedding: list[float] | None = None


@dataclass
class MemoryIndex:
    """index.json 的内存表示。"""
    user_profile_updated_at: str = ""
    topics: list[TopicIndexEntry] = field(default_factory=list)


@dataclass
class SearchResult:
    """一次主题记忆检索的单个结果。"""
    slug: str
    title: str
    entry: TopicMemoryEntry
    score: float
