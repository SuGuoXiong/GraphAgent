"""记忆系统数据类型定义（v2 重构版）。

每条记忆拥有唯一 ID、完整元数据和独立生命周期管理。
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ── 枚举类型 ────────────────────────────────────────────────


class MemoryType(str, Enum):
    """记忆类型枚举。"""
    USER_PREFERENCE = "user_preference"
    ARCH_DESIGN = "arch_design"
    PROJECT_RULE = "project_rule"
    DESIGN_PATTERN = "design_pattern"
    BUG_PITFALL = "bug_pitfall"
    GENERAL_KNOWLEDGE = "general_knowledge"


class MemoryScope(str, Enum):
    """记忆作用范围枚举。"""
    USER = "user"
    PROJECT = "project"
    SESSION = "session"


class MemoryStatus(str, Enum):
    """记忆生命周期状态枚举。"""
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    INVALID = "INVALID"


# ── 辅助函数 ────────────────────────────────────────────────


def _now() -> str:
    """返回当前 UTC 时间 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_str() -> str:
    """返回当天日期字符串 YYYY-MM-DD。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def generate_memory_id() -> str:
    """生成唯一记忆 ID，格式: {date}_{time}_{6位随机码}。

    示例: 2026-06-04_20-29_asdfgh
    """
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y-%m-%d")
    time_part = now.strftime("%H-%M")
    alphabet = string.ascii_lowercase + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"{date_part}_{time_part}_{random_part}"


def extract_date_from_id(memory_id: str) -> str:
    """从记忆 ID 中提取创建日期（前 10 位）。

    >>> extract_date_from_id("2026-06-04_20-29_asdfgh")
    '2026-06-04'
    """
    return memory_id[:10]


# ── 数据类 ──────────────────────────────────────────────────


@dataclass
class MemoryRecord:
    """单条记忆的完整数据模型。

    对应 Markdown 文件中一个 H2 区块的内容。
    """
    id: str = field(default_factory=generate_memory_id)
    type: MemoryType = MemoryType.GENERAL_KNOWLEDGE
    scope: MemoryScope = MemoryScope.PROJECT
    status: MemoryStatus = MemoryStatus.ACTIVE
    tags: list[str] = field(default_factory=list)
    session_id: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    access_at: str = field(default_factory=_now)
    access_count: int = 0
    content: str = ""

    def touch_access(self) -> None:
        """更新访问时间和计数。"""
        self.access_at = _now()
        self.access_count += 1

    def to_dict(self) -> dict:
        """转换为简化 dict（用于上下文注入和 API 返回）。"""
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, MemoryType) else self.type,
            "scope": self.scope.value if isinstance(self.scope, MemoryScope) else self.scope,
            "status": self.status.value if isinstance(self.status, MemoryStatus) else self.status,
            "tags": self.tags,
            "session_id": self.session_id,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_at": self.access_at,
            "access_count": self.access_count,
        }

    def derive_file_path(self) -> str:
        """从 ID 和 scope 推导文件相对路径。

        >>> MemoryRecord(id="2026-06-04_20-29_asdfgh", scope=MemoryScope.USER).derive_file_path()
        'user/2026-06-04.md'
        """
        scope_str = self.scope.value if isinstance(self.scope, MemoryScope) else self.scope
        date_str = extract_date_from_id(self.id)
        return f"{scope_str}/{date_str}.md"


@dataclass
class SearchMemoryInput:
    """search_memory 工具的输入参数。"""
    query: str
    type: str | None = None
    scope: str | None = None
    include_archived: bool = False
    limit: int = 3

    def __post_init__(self):
        if self.limit < 1:
            self.limit = 1
        elif self.limit > 3:
            self.limit = 3


@dataclass
class SearchMemoryResult:
    """search_memory 工具返回的单条记忆。"""
    id: str
    type: str
    scope: str
    status: str
    tags: list[str]
    content: str
    created_at: str
    updated_at: str
    relevance: float = 0.0

    @classmethod
    def from_record(cls, record: MemoryRecord, relevance: float = 0.0) -> "SearchMemoryResult":
        """从 MemoryRecord 创建搜索结果。"""
        return cls(
            id=record.id,
            type=record.type.value if isinstance(record.type, MemoryType) else record.type,
            scope=record.scope.value if isinstance(record.scope, MemoryScope) else record.scope,
            status=record.status.value if isinstance(record.status, MemoryStatus) else record.status,
            tags=list(record.tags),
            content=record.content,
            created_at=record.created_at,
            updated_at=record.updated_at,
            relevance=relevance,
        )


# ── 兼容旧 API 的类型别名 ──────────────────────────────────

# 保留旧类型以供迁移脚本和兼容代码使用
from dataclasses import dataclass as _dc
from typing import TYPE_CHECKING

# 旧类型保留在此处以避免 migration.py 导入失败
UserPreferenceItem = None  # 由旧代码覆盖，此处仅占位
UserProfile = None
TopicMemoryEntry = None
TopicMemory = None
TopicIndexEntry = None
MemoryIndex = None
SearchResult = None
