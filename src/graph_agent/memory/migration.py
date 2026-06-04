"""数据迁移脚本 —— 将 v1 格式记忆迁移到 v2 格式。

迁移范围:
    - user_profile.md → 多条 type=user_preference, scope=user 的记忆
    - topic/*.md → 多条按内容推断 type 的记忆
    - archive/ → status=ARCHIVED 的记忆
    - index.json → 废弃（由 FTS5 替代）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from graph_agent.memory.memory_types import (
    MemoryRecord,
    MemoryType,
    MemoryScope,
    MemoryStatus,
    _now,
    generate_memory_id,
)
from graph_agent.memory.memory_store import MemoryStore

logger = logging.getLogger(__name__)


def migrate_v1_to_v2(old_dir: Path, new_dir: Path) -> dict:
    """将 v1 格式的记忆迁移到 v2 格式。

    Args:
        old_dir: 旧数据目录（包含 user_profile.md, topic/, index.json, archive/）
        new_dir: 新数据目录

    Returns:
        {"migrated": N, "skipped": M, "errors": [...]}
    """
    stats = {"migrated": 0, "skipped": 0, "errors": []}
    store = MemoryStore(new_dir)
    store.ensure_dirs()

    # Buffer: (scope, date_str) → records
    daily_buffers: dict[tuple[str, str], list[MemoryRecord]] = {}

    # 1. 迁移 user_profile.md → user_preference 记忆
    profile_path = old_dir / "user_profile.md"
    if profile_path.exists():
        try:
            records = _migrate_profile(profile_path)
            for rec in records:
                key = (rec.scope.value, rec.id[:10])
                daily_buffers.setdefault(key, []).append(rec)
            stats["migrated"] += len(records)
        except Exception as e:
            stats["errors"].append({"file": str(profile_path), "error": str(e)})

    # 2. 迁移 topic/*.md → 按内容推断 type
    topic_dir = old_dir / "topic"
    if topic_dir.exists():
        for topic_file in topic_dir.glob("*.md"):
            try:
                records = _migrate_topic(topic_file)
                for rec in records:
                    key = (rec.scope.value, rec.id[:10])
                    daily_buffers.setdefault(key, []).append(rec)
                stats["migrated"] += len(records)
            except Exception as e:
                stats["errors"].append({"file": str(topic_file), "error": str(e)})

    # 3. 迁移 archive/ → ARCHIVED
    archive_dir = old_dir / "archive"
    if archive_dir.exists():
        for archive_file in archive_dir.glob("*.md"):
            try:
                records = _migrate_archive(archive_file)
                for rec in records:
                    key = ("archive", rec.id[:10])
                    daily_buffers.setdefault(key, []).append(rec)
                stats["migrated"] += len(records)
            except Exception as e:
                stats["errors"].append({"file": str(archive_file), "error": str(e)})

    # 4. 按 (scope, date_str) 分组写入每日文件
    for (scope, date_str), records in daily_buffers.items():
        try:
            store.save_daily_file(scope, date_str, records)
        except Exception as e:
            stats["errors"].append(
                {"file": f"{scope}/{date_str}.md", "error": str(e)}
            )
            stats["skipped"] += len(records)

    logger.info(
        f"迁移完成: migrated={stats['migrated']}, skipped={stats['skipped']}, "
        f"errors={len(stats['errors'])}"
    )
    return stats


def _migrate_profile(path: Path) -> list[MemoryRecord]:
    """迁移 user_profile.md → MemoryRecord 列表。"""
    records: list[MemoryRecord] = []

    text = path.read_text("utf-8")
    _, body = _parse_frontmatter(text)
    if not body:
        return records

    current_category = ""
    for line in body.split("\n"):
        h2_match = line.startswith("## ")
        if h2_match:
            current_category = line[3:].strip()
            continue

        item_match = line.strip().startswith("- ") and not line.strip().startswith("- **")
        if item_match and current_category:
            content = line.strip()[2:]
            # 去除 stale marker
            if content.startswith("⚠️ "):
                content = content[3:]
            if content:
                records.append(MemoryRecord(
                    id=generate_memory_id(),
                    type=MemoryType.USER_PREFERENCE,
                    scope=MemoryScope.USER,
                    status=MemoryStatus.ACTIVE,
                    tags=_extract_tags(content, max_tags=3),
                    content=f"[{current_category}] {content}",
                ))

    return records


def _migrate_topic(path: Path) -> list[MemoryRecord]:
    """迁移 topic/*.md → MemoryRecord 列表。"""
    records: list[MemoryRecord] = []

    text = path.read_text("utf-8")
    frontmatter, body = _parse_frontmatter(text)
    if not body:
        return records

    keywords = frontmatter.get("keywords", [])
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except json.JSONDecodeError:
            keywords = [k.strip() for k in keywords.split(",")]

    # 解析旧格式的 H3 条目
    import re
    current_entry: dict = {}
    entries: list[dict] = []

    for line in body.split("\n"):
        h3_match = re.match(r"^###\s+(.+)", line)
        if h3_match:
            if current_entry and current_entry.get("title"):
                entries.append(current_entry)
            header = h3_match.group(1).strip()
            if " — " in header:
                date, title = header.split(" — ", 1)
                current_entry = {"date": date, "title": title}
            else:
                current_entry = {"title": header}
            continue

        field_match = re.match(r"^[-*]\s+\*\*(.+?)\*\*:\s*(.*)", line)
        if field_match:
            key_map = {
                "任务": "task", "方案": "approach", "经验": "lessons",
                "用户反馈": "user_feedback", "用户调整": "user_adjustment",
            }
            field = key_map.get(field_match.group(1).strip(), field_match.group(1).strip())
            value = field_match.group(2).strip()
            current_entry[field] = value
            continue

    if current_entry and current_entry.get("title"):
        entries.append(current_entry)

    for entry in entries:
        inferred_type = _infer_type(entry, keywords)
        records.append(MemoryRecord(
            id=generate_memory_id(),
            type=inferred_type,
            scope=MemoryScope.PROJECT,
            status=MemoryStatus.ACTIVE,
            tags=keywords[:3],
            content=_format_entry(entry),
        ))

    return records


def _migrate_archive(path: Path) -> list[MemoryRecord]:
    """迁移 archive/*.md → ARCHIVED 状态的 MemoryRecord 列表。"""
    records: list[MemoryRecord] = []

    text = path.read_text("utf-8")
    _, body = _parse_frontmatter(text)
    if not body:
        return records

    # 简单处理：将整段归档内容作为一条记忆
    content = body.strip()
    if content:
        records.append(MemoryRecord(
            id=generate_memory_id(),
            type=MemoryType.GENERAL_KNOWLEDGE,
            scope=MemoryScope.PROJECT,
            status=MemoryStatus.ARCHIVED,
            tags=["归档"],
            content=content[:2000],  # 限制长度
        ))

    return records


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter。"""
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


def _extract_tags(content: str, max_tags: int = 3) -> list[str]:
    """从内容中提取关键词作为 tags。"""
    import re
    chinese = re.findall(r'[一-鿿]{2,}', content)
    english = re.findall(r'[a-zA-Z]{3,}', content.lower())
    candidates = chinese + english
    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
        if len(result) >= max_tags:
            break
    return result if result else ["通用"]


def _infer_type(entry: dict, keywords: list[str]) -> MemoryType:
    """从条目内容和关键词推断记忆类型。"""
    combined = " ".join(keywords) + " " + entry.get("title", "") + " " + entry.get("task", "")

    bug_kw = ["bug", "错误", "报错", "异常", "修复", "故障"]
    arch_kw = ["架构", "设计", "选型", "模块", "分层"]
    pattern_kw = ["模式", "接口", "封装", "组件"]
    rule_kw = ["规范", "编码", "命名", "约定"]

    if any(kw in combined for kw in bug_kw):
        return MemoryType.BUG_PITFALL
    if any(kw in combined for kw in arch_kw):
        return MemoryType.ARCH_DESIGN
    if any(kw in combined for kw in pattern_kw):
        return MemoryType.DESIGN_PATTERN
    if any(kw in combined for kw in rule_kw):
        return MemoryType.PROJECT_RULE

    return MemoryType.GENERAL_KNOWLEDGE


def _format_entry(entry: dict) -> str:
    """将旧条目格式化为 content 文本。"""
    parts = []
    title = entry.get("title", "")
    if title:
        parts.append(f"## {title}")
    task = entry.get("task", "")
    if task:
        parts.append(f"**任务**: {task}")
    approach = entry.get("approach", "")
    if approach:
        parts.append(f"**方案**: {approach}")
    lessons = entry.get("lessons", "")
    if lessons:
        parts.append(f"**经验**: {lessons}")
    return "\n".join(parts)
