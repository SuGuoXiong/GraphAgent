"""记忆存储层（v2 重构版）—— 每日 Markdown 文件读写。

目录结构:
    data/memory/
    ├── user/{date}.md
    ├── project/{date}.md
    ├── session/{date}.md
    ├── archive/{date}.md
    └── memory.db
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from graph_agent.memory.memory_types import (
    MemoryRecord,
    MemoryType,
    MemoryScope,
    MemoryStatus,
    _now,
    _today_str,
    generate_memory_id,
    extract_date_from_id,
)

if TYPE_CHECKING:
    pass


def _get_base_dir() -> Path:
    return Path(os.getenv("GRAPHAGENT_MEMORY_DIR", "data/memory"))


class MemoryStore:
    """每日 Markdown 文件存储层。

    职责:
    - 按 scope/date 组织 Markdown 文件
    - 文件级锁（防并发竞态）
    - atomic_write（防崩溃损坏）
    - 正文缩进保护（防 ## / --- 误解析）
    - 记忆记录的增删改查
    """

    def __init__(self, base_dir: str | Path | None = None):
        self._base = Path(base_dir) if base_dir else _get_base_dir()
        self._file_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @property
    def base_dir(self) -> Path:
        return self._base

    # ── 目录管理 ──────────────────────────────────────────

    def ensure_dirs(self) -> None:
        """确保存储目录结构存在。"""
        for subdir in ("user", "project", "session", "archive"):
            (self._base / subdir).mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, scope: str, date_str: str) -> Path:
        """获取每日文件完整路径。"""
        return self._base / scope / f"{date_str}.md"

    # ── 文件级锁 ──────────────────────────────────────────

    def _acquire_lock(self, file_path: str) -> threading.Lock:
        """获取文件级锁（线程安全地创建/获取 Lock）。"""
        with self._locks_guard:
            if file_path not in self._file_locks:
                self._file_locks[file_path] = threading.Lock()
            return self._file_locks[file_path]

    # ── Markdown 解析与序列化 ──────────────────────────────

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict, str]:
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

    @staticmethod
    def _serialize_memory_to_block(record: MemoryRecord) -> str:
        """将单条记忆序列化为 Markdown H2 区块。"""
        scope_str = record.scope.value if isinstance(record.scope, MemoryScope) else record.scope
        type_str = record.type.value if isinstance(record.type, MemoryType) else record.type
        status_str = record.status.value if isinstance(record.status, MemoryStatus) else record.status

        lines = [f"## {record.id}"]
        lines.append(f"- **type**: {type_str}")
        lines.append(f"- **status**: {status_str}")
        lines.append("- **tags**:")
        for tag in record.tags:
            lines.append(f"  - {tag}")
        lines.append(f'- **created_at**: "{record.created_at}"')
        lines.append(f'- **updated_at**: "{record.updated_at}"')
        lines.append(f'- **access_at**: "{record.access_at}"')
        lines.append(f"- **access_count**: {record.access_count}")
        if record.session_id and scope_str == "session":
            lines.append(f'- **session_id**: "{record.session_id}"')
        lines.append("")

        # 正文每行加 4 空格缩进
        for content_line in record.content.split("\n"):
            lines.append(f"    {content_line}" if content_line.strip() else "")

        return "\n".join(lines)

    @staticmethod
    def _parse_block_to_record(block: str, scope: str | None = None) -> MemoryRecord | None:
        """从 H2 区块文本解析为 MemoryRecord。

        区块格式:
            ## {id}
            - **key**: value
            ...

            {缩进的正文}
        """
        block = block.strip()
        if not block:
            return None

        lines = block.split("\n")
        if not lines:
            return None

        # 解析 H2 标题 → id
        h2_match = re.match(r"^##\s+(.+)", lines[0])
        if not h2_match:
            return None
        memory_id = h2_match.group(1).strip()

        # 解析元数据键值对
        metadata: dict[str, str] = {}
        content_start_idx = 1
        tag_lines: list[str] = []
        in_tags = False

        for i in range(1, len(lines)):
            line = lines[i]

            # 检测 tags 多行列表
            tag_match = re.match(r"^\s*-\s+(.+)$", line)
            if in_tags and tag_match and not line.startswith("- **"):
                tag_lines.append(tag_match.group(1).strip())
                continue

            # 检测元数据行: - **key**: value
            kv_match = re.match(r"^-\s+\*\*(.+?)\*\*:\s*(.*)", line)
            if kv_match:
                in_tags = False
                key = kv_match.group(1).strip()
                value = kv_match.group(2).strip().strip('"')
                metadata[key] = value
                if key == "tags":
                    in_tags = True
                continue

            # 空行 → 元数据结束，正文开始
            if line.strip() == "" and i > 1:
                content_start_idx = i + 1
                break

            # 非空非元数据行 → 正文已开始
            if line.strip() and not kv_match:
                content_start_idx = i
                break

        # 提取正文（去除每行 4 空格缩进）
        content_lines: list[str] = []
        for i in range(content_start_idx, len(lines)):
            line = lines[i]
            if line.startswith("    "):
                content_lines.append(line[4:])
            elif line.strip() == "":
                content_lines.append("")
            elif line.strip():
                content_lines.append(line)

        content = "\n".join(content_lines).strip()

        # 构造 MemoryRecord
        type_str = metadata.get("type", "general_knowledge")
        scope_str = metadata.get("scope", scope or "project")
        status_str = metadata.get("status", "ACTIVE")

        try:
            record_type = MemoryType(type_str)
        except ValueError:
            record_type = MemoryType.GENERAL_KNOWLEDGE
        try:
            record_scope = MemoryScope(scope_str)
        except ValueError:
            record_scope = MemoryScope.PROJECT
        try:
            record_status = MemoryStatus(status_str)
        except ValueError:
            record_status = MemoryStatus.ACTIVE

        return MemoryRecord(
            id=memory_id,
            type=record_type,
            scope=record_scope,
            status=record_status,
            tags=tag_lines if tag_lines else [],
            session_id=metadata.get("session_id"),
            created_at=metadata.get("created_at", _now()),
            updated_at=metadata.get("updated_at", _now()),
            access_at=metadata.get("access_at", _now()),
            access_count=int(metadata.get("access_count", 0)),
            content=content,
        )

    # ── 文件级读写 ────────────────────────────────────────

    def load_daily_file(self, scope: str, date_str: str) -> list[MemoryRecord]:
        """加载指定日期的文件，返回所有记忆记录。"""
        file_path = self._get_file_path(scope, date_str)
        if not file_path.exists():
            return []

        text = file_path.read_text("utf-8")
        _, body = self._parse_frontmatter(text)
        if not body:
            return []

        # 按 H2 分割区块
        blocks = re.split(r"\n(?=## )", body)
        records: list[MemoryRecord] = []
        for block in blocks:
            record = self._parse_block_to_record(block, scope)
            if record:
                records.append(record)
        return records

    def save_daily_file(self, scope: str, date_str: str, records: list[MemoryRecord]) -> None:
        """将记忆记录列表写入每日文件（atomic write）。"""
        file_path = self._get_file_path(scope, date_str)
        scope_str = scope

        # 构建文件内容
        parts = ["---"]
        parts.append(f'date: "{date_str}"')
        parts.append(f'scope: "{scope_str}"')
        parts.append(f"memory_count: {len(records)}")
        parts.append("---")
        parts.append("")
        parts.append(f"# {date_str} 记忆记录 ({scope_str})")
        parts.append("")

        for i, record in enumerate(records):
            parts.append(self._serialize_memory_to_block(record))
            if i < len(records) - 1:
                parts.append("---")
                parts.append("")

        content = "\n".join(parts) + "\n"

        # atomic_write: 先写临时文件 → os.replace 原子替换
        self.ensure_dirs()
        fd, tmp_path = tempfile.mkstemp(
            suffix=".md", prefix=".tmp_", dir=file_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, file_path)
        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _append_record_locked(self, record: MemoryRecord) -> None:
        """向每日文件追加记忆记录（调用方已持锁）。"""
        scope_str = record.scope.value if isinstance(record.scope, MemoryScope) else record.scope
        date_str = extract_date_from_id(record.id)
        existing = self.load_daily_file(scope_str, date_str)
        existing.append(record)
        self.save_daily_file(scope_str, date_str, existing)

    def append_record(self, record: MemoryRecord) -> None:
        """向每日文件追加一条记忆记录（线程安全）。"""
        scope_str = record.scope.value if isinstance(record.scope, MemoryScope) else record.scope
        date_str = extract_date_from_id(record.id)
        file_path_str = f"{scope_str}/{date_str}.md"
        lock = self._acquire_lock(file_path_str)
        with lock:
            self._append_record_locked(record)

    def _remove_record_locked(
        self, memory_id: str, scope: str, date_str: str
    ) -> MemoryRecord | None:
        """从每日文件移除记忆记录（调用方已持锁）。"""
        file_path = self._get_file_path(scope, date_str)
        existing = self.load_daily_file(scope, date_str)
        removed = None
        new_records = []
        for rec in existing:
            if rec.id == memory_id:
                removed = rec
            else:
                new_records.append(rec)
        if removed:
            if new_records:
                self.save_daily_file(scope, date_str, new_records)
            elif file_path.exists():
                file_path.unlink()
        return removed

    def remove_record(self, memory_id: str, scope: str, date_str: str) -> MemoryRecord | None:
        """从每日文件中移除一条记忆记录（线程安全），返回被移除的记录。"""
        file_path_str = f"{scope}/{date_str}.md"
        lock = self._acquire_lock(file_path_str)
        with lock:
            return self._remove_record_locked(memory_id, scope, date_str)

    def _update_record_locked(self, memory_id: str, updated: MemoryRecord, scope: str) -> None:
        """更新每日文件中的记忆记录（调用方已持锁）。"""
        date_str = extract_date_from_id(memory_id)
        existing = self.load_daily_file(scope, date_str)
        for i, rec in enumerate(existing):
            if rec.id == memory_id:
                existing[i] = updated
                self.save_daily_file(scope, date_str, existing)
                return

    def update_record(self, memory_id: str, updated: MemoryRecord, scope: str) -> None:
        """更新每日文件中的一条记忆记录（线程安全）。"""
        date_str = extract_date_from_id(memory_id)
        file_path_str = f"{scope}/{date_str}.md"
        lock = self._acquire_lock(file_path_str)
        with lock:
            self._update_record_locked(memory_id, updated, scope)

    def move_record_between_files(
        self,
        memory_id: str,
        from_scope: str,
        from_date: str,
        to_scope: str,
        to_date: str,
    ) -> MemoryRecord | None:
        """将记忆记录从一个文件移动到另一个文件（用于归档和唤醒）。

        这是一个复合操作，需要获取两个文件的锁。为避免死锁，按路径字母序获取。
        """
        from_path = f"{from_scope}/{from_date}.md"
        to_path = f"{to_scope}/{to_date}.md"

        # 按路径字母序获取锁，避免死锁
        first, second = sorted([from_path, to_path])
        lock1 = self._acquire_lock(first)
        lock2 = self._acquire_lock(second)

        with lock1:
            if first != second:
                with lock2:
                    return self._move_record_impl(
                        memory_id, from_scope, from_date, to_scope, to_date
                    )
            else:
                return self._move_record_impl(
                    memory_id, from_scope, from_date, to_scope, to_date
                )

    def _move_record_impl(
        self, memory_id: str, from_scope: str, from_date: str, to_scope: str, to_date: str
    ) -> MemoryRecord | None:
        """move_record 的内部实现（调用方已持两个文件的锁）。"""
        # 从源文件移除（使用 _locked 版本避免重复加锁）
        removed = self._remove_record_locked(memory_id, from_scope, from_date)
        if not removed:
            return None

        # 追加到目标文件（使用 _locked 版本避免重复加锁）
        self._append_record_locked(removed)
        return removed

    def delete_empty_file(self, scope: str, date_str: str) -> bool:
        """删除空的每日文件，返回是否执行了删除。"""
        file_path = self._get_file_path(scope, date_str)
        if not file_path.exists():
            return False
        records = self.load_daily_file(scope, date_str)
        if not records:
            file_path.unlink()
            return True
        return False

    def list_all_file_keys(self) -> list[tuple[str, str]]:
        """列出所有每日文件键 (scope, date_str)。"""
        keys: list[tuple[str, str]] = []
        self.ensure_dirs()
        for scope_dir_name in ("user", "project", "session", "archive"):
            scope_path = self._base / scope_dir_name
            if not scope_path.exists():
                continue
            for md_file in scope_path.glob("*.md"):
                date_str = md_file.stem  # "2026-06-04"
                keys.append((scope_dir_name, date_str))
        return keys
