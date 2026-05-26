"""记忆存储层 —— 文件读写、index.json 管理、去重合并。

目录结构:
    data/memory/
    ├── user_profile.md
    ├── topic/
    │   └── {slug}.md
    ├── index.json
    └── archive/
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from graph_agent.memory.memory_types import (
    UserProfile,
    UserPreferenceItem,
    TopicMemory,
    TopicMemoryEntry,
    TopicIndexEntry,
    MemoryIndex,
    _now,
)

if TYPE_CHECKING:
    pass


def _get_base_dir() -> Path:
    return Path(os.getenv("GRAPHAGENT_MEMORY_DIR", "data/memory"))


class MemoryStore:
    """记忆文件存储 —— 负责 markdown + JSON 文件的读写和索引维护。"""

    def __init__(self, base_dir: str | Path | None = None):
        self._base = Path(base_dir) if base_dir else _get_base_dir()
        self._lock = threading.Lock()

    @property
    def base_dir(self) -> Path:
        return self._base

    def ensure_dirs(self) -> None:
        """确保存储目录结构存在。"""
        (self._base / "topic").mkdir(parents=True, exist_ok=True)
        (self._base / "archive").mkdir(parents=True, exist_ok=True)

    # ── User Profile ──────────────────────────────────────

    def load_profile(self) -> UserProfile:
        """加载用户画像，不存在则返回空。"""
        path = self._base / "user_profile.md"
        if not path.exists():
            return UserProfile()
        frontmatter, body = _parse_markdown_with_frontmatter(path.read_text("utf-8"))
        prefs = self._parse_profile_body(body)
        return UserProfile(
            preferences=prefs,
            updated_at=frontmatter.get("updated_at", ""),
            total_extractions=frontmatter.get("total_extractions", 0),
        )

    def save_profile(self, profile: UserProfile) -> None:
        """保存用户画像到文件。"""
        with self._lock:
            self.ensure_dirs()
            profile.updated_at = _now()
            profile.total_extractions += 1

            parts = ["---"]
            parts.append(f"updated_at: \"{profile.updated_at}\"")
            parts.append(f"total_extractions: {profile.total_extractions}")
            parts.append("---")
            parts.append("")
            parts.append("# 用户画像")
            parts.append("")
            for category, items in profile.preferences.items():
                parts.append(f"## {category}")
                for item in items:
                    stale_marker = "⚠️ " if item.stale else ""
                    parts.append(f"- {stale_marker}{item.content}")
                parts.append("")

            (self._base / "user_profile.md").write_text(
                "\n".join(parts), "utf-8"
            )

    def _parse_profile_body(self, body: str) -> dict[str, list[UserPreferenceItem]]:
        """解析用户画像正文，提取按类别分组的偏好列表。"""
        prefs: dict[str, list[UserPreferenceItem]] = {}
        current_category = ""

        for line in body.split("\n"):
            h2 = re.match(r"^##\s+(.+)", line)
            if h2:
                current_category = h2.group(1).strip()
                if current_category not in prefs:
                    prefs[current_category] = []
                continue

            item_match = re.match(r"^[-*]\s+(?:⚠️\s*)?(.+)", line)
            if item_match and current_category:
                content = item_match.group(1).strip()
                stale = "⚠️" in line
                if content:
                    prefs[current_category].append(
                        UserPreferenceItem(
                            category=current_category,
                            content=content,
                            stale=stale,
                            confidence=0.5 if stale else 1.0,
                        )
                    )

        return prefs

    # ── Topic Memory ──────────────────────────────────────

    def load_topic(self, slug: str) -> TopicMemory | None:
        """加载单个主题记忆文件。"""
        path = self._base / "topic" / f"{slug}.md"
        if not path.exists():
            return None
        frontmatter, body = _parse_markdown_with_frontmatter(path.read_text("utf-8"))
        entries = self._parse_topic_entries(body)
        return TopicMemory(
            slug=slug,
            title=frontmatter.get("title", slug),
            keywords=frontmatter.get("keywords", []),
            summary=frontmatter.get("summary", ""),
            entries=entries,
            created_at=frontmatter.get("created_at", ""),
            updated_at=frontmatter.get("updated_at", ""),
            last_accessed_at=frontmatter.get("last_accessed_at", ""),
        )

    def save_topic(self, topic: TopicMemory) -> None:
        """保存主题记忆到文件。"""
        with self._lock:
            self.ensure_dirs()
            topic.updated_at = _now()

            parts = ["---"]
            parts.append(f"topic: {topic.slug}")
            parts.append(f"title: \"{topic.title}\"")
            keywords_str = json.dumps(topic.keywords, ensure_ascii=False)
            parts.append(f"keywords: {keywords_str}")
            parts.append(f"summary: \"{topic.summary}\"")
            parts.append(f"created_at: \"{topic.created_at}\"")
            parts.append(f"updated_at: \"{topic.updated_at}\"")
            parts.append(f"last_accessed_at: \"{topic.last_accessed_at}\"")
            parts.append(f"entry_count: {len(topic.entries)}")
            parts.append("---")
            parts.append("")
            parts.append(f"# {topic.title}")
            parts.append("")
            parts.append("## 记忆条目")
            parts.append("")

            for entry in topic.entries:
                parts.append(f"### {entry.date} — {entry.title}")
                parts.append(f"- **任务**: {entry.task}")
                parts.append(f"- **方案**: {entry.approach}")
                parts.append(f"- **经验**: ")
                for line in entry.lessons.split("\n"):
                    parts.append(f"  {line}")
                if entry.user_feedback:
                    parts.append(f"- **用户反馈**: {entry.user_feedback}")
                if entry.user_adjustment:
                    parts.append(f"- **用户调整**: {entry.user_adjustment}")
                parts.append("")

            (self._base / "topic" / f"{topic.slug}.md").write_text(
                "\n".join(parts), "utf-8"
            )

    def _parse_topic_entries(self, body: str) -> list[TopicMemoryEntry]:
        """解析主题记忆正文，提取记忆条目列表。"""
        entries: list[TopicMemoryEntry] = []
        current_entry: dict[str, str] = {}
        current_field = ""

        for line in body.split("\n"):
            h3 = re.match(r"^###\s+(.+)", line)
            if h3:
                if current_entry and current_entry.get("title"):
                    entries.append(TopicMemoryEntry(
                        title=current_entry.get("title", ""),
                        task=current_entry.get("task", ""),
                        approach=current_entry.get("approach", ""),
                        lessons=current_entry.get("lessons", ""),
                        user_feedback=current_entry.get("user_feedback", ""),
                        user_adjustment=current_entry.get("user_adjustment", ""),
                        date=current_entry.get("date", ""),
                    ))
                header = h3.group(1).strip()
                if " — " in header:
                    date, title = header.split(" — ", 1)
                    current_entry = {"date": date, "title": title}
                else:
                    current_entry = {"title": header}
                current_field = ""
                continue

            field_match = re.match(r"^[-*]\s+\*\*(.+?)\*\*:\s*(.*)", line)
            if field_match:
                current_field = field_match.group(1).strip()
                value = field_match.group(2).strip()
                key_map = {"任务": "task", "方案": "approach", "经验": "lessons",
                           "用户反馈": "user_feedback", "用户调整": "user_adjustment"}
                mapped = key_map.get(current_field, current_field)
                current_entry[mapped] = value
                continue

            # Continuation lines for multi-line fields
            if current_field and current_entry and line.strip() and not line.startswith("#"):
                mapped = {"任务": "task", "方案": "approach", "经验": "lessons",
                         "用户反馈": "user_feedback", "用户调整": "user_adjustment"}
                key = mapped.get(current_field, current_field)
                existing = current_entry.get(key, "")
                if existing:
                    current_entry[key] = existing + "\n" + line.strip()

        if current_entry and current_entry.get("title"):
            entries.append(TopicMemoryEntry(
                title=current_entry.get("title", ""),
                task=current_entry.get("task", ""),
                approach=current_entry.get("approach", ""),
                lessons=current_entry.get("lessons", ""),
                user_feedback=current_entry.get("user_feedback", ""),
                user_adjustment=current_entry.get("user_adjustment", ""),
                date=current_entry.get("date", ""),
            ))

        return entries

    def archive_topic_entry(self, slug: str, entry_index: int) -> None:
        """将指定条目归档（根据时间衰减策略移除过期条目时调用）。"""
        topic = self.load_topic(slug)
        if not topic or entry_index >= len(topic.entries):
            return
        removed = topic.entries.pop(entry_index)
        topic.updated_at = _now()
        self.save_topic(topic)

        # 写入归档文件
        archive_path = self._base / "archive" / f"{slug}_{removed.date}.md"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(
            f"# [归档] {removed.title}\n"
            f"- 日期: {removed.date}\n"
            f"- 任务: {removed.task}\n"
            f"- 方案: {removed.approach}\n"
            f"- 归档时间: {_now()}\n",
            "utf-8",
        )

    # ── Index ─────────────────────────────────────────────

    def load_index(self) -> MemoryIndex:
        """加载 index.json。"""
        path = self._base / "index.json"
        if not path.exists():
            return MemoryIndex()
        try:
            data = json.loads(path.read_text("utf-8"))
            topics = [
                TopicIndexEntry(**t) for t in data.get("topics", [])
            ]
            return MemoryIndex(
                user_profile_updated_at=data.get("user_profile_updated_at", ""),
                topics=topics,
            )
        except (json.JSONDecodeError, KeyError):
            return MemoryIndex()

    def save_index(self, index: MemoryIndex) -> None:
        """保存 index.json。"""
        with self._lock:
            self.ensure_dirs()
            data = {
                "user_profile_updated_at": index.user_profile_updated_at,
                "topics": [
                    {
                        "slug": t.slug,
                        "title": t.title,
                        "path": t.path,
                        "keywords": t.keywords,
                        "summary": t.summary,
                        "created_at": t.created_at,
                        "updated_at": t.updated_at,
                        "last_accessed_at": t.last_accessed_at,
                        "entry_count": t.entry_count,
                        "embedding": t.embedding,
                    }
                    for t in index.topics
                ],
            }
            (self._base / "index.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), "utf-8"
            )

    def touch_topic_access(self, slug: str) -> None:
        """更新指定 topic 的 last_accessed_at。"""
        index = self.load_index()
        for t in index.topics:
            if t.slug == slug:
                t.last_accessed_at = _now()
                break
        self.save_index(index)

    def upsert_index_entry(self, topic: TopicMemory) -> None:
        """在索引中插入或更新一个 topic 条目。"""
        index = self.load_index()
        for i, t in enumerate(index.topics):
            if t.slug == topic.slug:
                index.topics[i] = TopicIndexEntry(
                    slug=topic.slug,
                    title=topic.title,
                    path=f"topic/{topic.slug}.md",
                    keywords=topic.keywords,
                    summary=topic.summary,
                    created_at=topic.created_at,
                    updated_at=topic.updated_at,
                    last_accessed_at=topic.last_accessed_at,
                    entry_count=len(topic.entries),
                )
                break
        else:
            index.topics.append(TopicIndexEntry(
                slug=topic.slug,
                title=topic.title,
                path=f"topic/{topic.slug}.md",
                keywords=topic.keywords,
                summary=topic.summary,
                created_at=topic.created_at,
                updated_at=topic.updated_at,
                last_accessed_at=topic.last_accessed_at,
                entry_count=len(topic.entries),
            ))
        self.save_index(index)


# ── Helpers ────────────────────────────────────────────────

def _parse_markdown_with_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter + Markdown 正文。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        frontmatter = {}
    return frontmatter, parts[2].strip()
