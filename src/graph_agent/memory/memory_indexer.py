"""SQLite FTS5 索引层 —— 全文检索索引管理。

提供:
- FTS5 表创建与初始化（WAL 模式）
- 记忆记录的索引 CRUD
- 影子表切换的索引重建
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from graph_agent.memory.memory_types import (
    MemoryRecord,
    MemoryStatus,
    _now,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# SQL 语句常量
_CREATE_META_TABLE = """
CREATE TABLE IF NOT EXISTS memory_meta (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    scope        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ACTIVE',
    tags         TEXT NOT NULL DEFAULT '',
    session_id   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    access_at    TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    file_path    TEXT NOT NULL
);
"""

_CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    memory_id UNINDEXED,
    tags,
    content,
    tokenize='unicode61'
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_meta_type ON memory_meta(type);",
    "CREATE INDEX IF NOT EXISTS idx_meta_scope ON memory_meta(scope);",
    "CREATE INDEX IF NOT EXISTS idx_meta_status ON memory_meta(status);",
    "CREATE INDEX IF NOT EXISTS idx_meta_session ON memory_meta(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_meta_access ON memory_meta(access_at DESC);",
]


class MemoryIndexer:
    """SQLite FTS5 索引管理器。

    职责:
    - 数据库初始化（表创建、索引创建、WAL 模式）
    - 记忆记录的索引增删改
    - 影子表切换的索引重建
    - 连接管理
    """

    def __init__(self, db_path: str | Path = "data/memory/memory.db"):
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    # ── 连接管理 ──────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（惰性初始化）。"""
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=OFF;")
        return self._conn

    def init_db(self) -> None:
        """初始化数据库表结构（幂等操作）。"""
        conn = self._get_conn()
        conn.execute(_CREATE_META_TABLE)
        conn.execute(_CREATE_FTS_TABLE)
        for idx_sql in _CREATE_INDEXES:
            conn.execute(idx_sql)
        conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── 索引 CRUD ─────────────────────────────────────────

    def upsert_record(self, record: MemoryRecord) -> None:
        """插入或更新一条记忆记录的索引。"""
        conn = self._get_conn()
        scope_str = record.scope.value if hasattr(record.scope, 'value') else record.scope
        type_str = record.type.value if hasattr(record.type, 'value') else record.type
        status_str = record.status.value if hasattr(record.status, 'value') else record.status

        conn.execute(
            """INSERT OR REPLACE INTO memory_meta
               (id, type, scope, status, tags, session_id,
                created_at, updated_at, access_at, access_count, file_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                type_str,
                scope_str,
                status_str,
                json.dumps(record.tags, ensure_ascii=False),
                record.session_id,
                record.created_at,
                record.updated_at,
                record.access_at,
                record.access_count,
                record.derive_file_path(),
            ),
        )

        # FTS5: DELETE + INSERT（FTS5 不支持 UPDATE）
        conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (record.id,))
        conn.execute(
            "INSERT INTO memory_fts (memory_id, tags, content) VALUES (?, ?, ?)",
            (record.id, " ".join(record.tags), record.content),
        )
        conn.commit()

    def delete_record(self, memory_id: str) -> None:
        """从索引中删除一条记忆记录。"""
        conn = self._get_conn()
        conn.execute("DELETE FROM memory_meta WHERE id = ?", (memory_id,))
        conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        conn.commit()

    def delete_fts_only(self, memory_id: str) -> None:
        """仅从 FTS5 索引中删除（用于 INVALID 状态，保留 meta 记录）。"""
        conn = self._get_conn()
        conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        conn.commit()

    def update_meta(self, memory_id: str, **kwargs) -> None:
        """更新 memory_meta 中的指定字段。"""
        if not kwargs:
            return
        conn = self._get_conn()
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [memory_id]
        conn.execute(
            f"UPDATE memory_meta SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()

    def touch_access(self, memory_id: str) -> None:
        """更新访问时间和计数（高频操作，仅更新 DB）。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE memory_meta SET access_at = ?, access_count = access_count + 1 WHERE id = ?",
            (_now(), memory_id),
        )
        conn.commit()

    def get_meta(self, memory_id: str) -> dict | None:
        """获取单条记忆的元数据。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memory_meta WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    # ── 检索 ──────────────────────────────────────────────

    def search_fts(
        self,
        query_pattern: str,
        status_filter: tuple[str, ...] = ("ACTIVE",),
        type_filter: str | None = None,
        scope_filter: str | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """执行 FTS5 全文检索。

        Returns:
            dict 列表，包含 meta 字段 + content + relevance 分数
        """
        conn = self._get_conn()

        # 构造查询参数
        params: list = []
        status_placeholders = ", ".join("?" for _ in status_filter)
        params.extend(status_filter)

        type_clause = ""
        if type_filter:
            type_clause = "AND m.type = ?"
            params.append(type_filter)

        scope_clause = ""
        if scope_filter:
            scope_clause = "AND m.scope = ?"
            params.append(scope_filter)

        params.append(limit)

        sql = f"""
            SELECT
                m.*, f.content,
                rank AS relevance
            FROM memory_fts f
            JOIN memory_meta m ON f.memory_id = m.id
            WHERE memory_fts MATCH ?
              AND m.status IN ({status_placeholders})
              {type_clause}
              {scope_clause}
            ORDER BY
                CASE m.scope
                    WHEN 'session' THEN 3
                    WHEN 'project' THEN 2
                    WHEN 'user' THEN 1
                    ELSE 0
                END DESC,
                relevance DESC
            LIMIT ?
        """

        rows = conn.execute(sql, [query_pattern] + params).fetchall()
        return [dict(r) for r in rows]

    def search_fallback(
        self,
        keyword: str,
        status_filter: tuple[str, ...] = ("ACTIVE",),
        type_filter: str | None = None,
        scope_filter: str | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """降级检索：对 tags 列做 LIKE 模糊匹配。"""
        conn = self._get_conn()

        params: list = []
        status_placeholders = ", ".join("?" for _ in status_filter)
        params.extend(status_filter)

        type_clause = ""
        if type_filter:
            type_clause = "AND m.type = ?"
            params.append(type_filter)

        scope_clause = ""
        if scope_filter:
            scope_clause = "AND m.scope = ?"
            params.append(scope_filter)

        params.append(limit)

        sql = f"""
            SELECT m.*, f.content, 0.5 AS relevance
            FROM memory_meta m
            LEFT JOIN memory_fts f ON f.memory_id = m.id
            WHERE m.tags LIKE '%' || ? || '%'
              AND m.status IN ({status_placeholders})
              {type_clause}
              {scope_clause}
            ORDER BY m.access_at DESC
            LIMIT ?
        """

        rows = conn.execute(sql, [keyword] + params).fetchall()
        return [dict(r) for r in rows]

    # ── 维护查询 ──────────────────────────────────────────

    def count_by_status(self, status: str) -> int:
        """统计指定状态的记忆数量。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_meta WHERE status = ?", (status,)
        ).fetchone()
        return row["cnt"] if row else 0

    def get_records_for_maintenance(
        self, status: str, min_age_days: int = 90, max_access_count: int = 2
    ) -> list[dict]:
        """获取需要维护的记忆记录（用于自动归档判断）。"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM memory_meta
               WHERE status = ?
                 AND access_count <= ?
                 AND updated_at < datetime('now', ? || ' days')
            """,
            (status, max_access_count, f"-{min_age_days}"),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_memories(self, session_id: str) -> list[dict]:
        """获取指定会话的所有 ACTIVE 记忆。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memory_meta WHERE session_id = ? AND status = 'ACTIVE'",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 索引重建 ──────────────────────────────────────────

    def rebuild_index(self, records_by_file: dict[tuple[str, str], list[MemoryRecord]]) -> dict:
        """从 MemoryRecord 列表重建 FTS5 索引（影子表切换）。

        Args:
            records_by_file: {(scope, date_str): [MemoryRecord, ...]}

        Returns:
            {"records": N, "success": X, "errors": [...]}
        """
        conn = self._get_conn()
        stats = {"records": 0, "success": 0, "errors": []}

        # Step 1: 创建影子表
        conn.execute("DROP TABLE IF EXISTS memory_meta_new")
        conn.execute("DROP TABLE IF EXISTS memory_fts_new")
        conn.execute(_CREATE_META_TABLE.replace("memory_meta", "memory_meta_new"))
        conn.execute(_CREATE_FTS_TABLE.replace("memory_fts", "memory_fts_new"))

        # Step 2: 填充数据
        for (_scope, _date), records in records_by_file.items():
            for record in records:
                stats["records"] += 1
                try:
                    scope_str = record.scope.value if hasattr(record.scope, 'value') else record.scope
                    type_str = record.type.value if hasattr(record.type, 'value') else record.type
                    status_str = record.status.value if hasattr(record.status, 'value') else record.status

                    conn.execute(
                        """INSERT INTO memory_meta_new
                           (id, type, scope, status, tags, session_id,
                            created_at, updated_at, access_at, access_count, file_path)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            record.id, type_str, scope_str, status_str,
                            json.dumps(record.tags, ensure_ascii=False),
                            record.session_id, record.created_at, record.updated_at,
                            record.access_at, record.access_count,
                            record.derive_file_path(),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO memory_fts_new (memory_id, tags, content) VALUES (?, ?, ?)",
                        (record.id, " ".join(record.tags), record.content),
                    )
                    stats["success"] += 1
                except Exception as e:
                    stats["errors"].append({"record_id": record.id, "error": str(e)})
                    logger.warning(f"重建索引失败 record={record.id}: {e}")

        # Commit shadow table data before exclusive switch
        conn.commit()

        # Step 3: 影子表切换（排他锁）
        try:
            conn.execute("PRAGMA locking_mode = EXCLUSIVE;")
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")

            conn.execute("DROP TABLE IF EXISTS memory_fts;")
            conn.execute("DROP TABLE IF EXISTS memory_meta;")
            conn.execute("ALTER TABLE memory_fts_new RENAME TO memory_fts;")
            conn.execute("ALTER TABLE memory_meta_new RENAME TO memory_meta;")

            # 重建辅助索引
            for idx_sql in _CREATE_INDEXES:
                conn.execute(idx_sql)

            conn.execute("COMMIT;")
        except Exception as e:
            conn.execute("ROLLBACK;")
            logger.error(f"影子表切换失败: {e}")
            raise
        finally:
            try:
                conn.execute("PRAGMA locking_mode = NORMAL;")
            except Exception:
                pass

        logger.info(
            f"索引重建完成: records={stats['records']}, success={stats['success']}, "
            f"errors={len(stats['errors'])}"
        )
        return stats

    def verify_consistency(self, expected_count: int) -> bool:
        """验证 memory_meta 记录数与文件 H2 区块数是否一致。"""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM memory_meta").fetchone()
        actual = row["cnt"] if row else 0
        return actual == expected_count
