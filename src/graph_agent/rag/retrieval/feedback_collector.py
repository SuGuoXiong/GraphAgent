"""检索反馈收集器。

追踪检索结果被 Agent 实际使用的情况，形成隐式反馈闭环。
长期积累的数据可用于优化融合权重和重排序策略。
"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional

from graph_agent.rag.rag_types import FeedbackStats, SearchResult

logger = logging.getLogger(__name__)


class FeedbackCollector:
    """检索反馈收集器。

    追踪检索结果的使用情况，形成隐式反馈闭环。
    数据存储在 SQLite 中（data/rag/.feedback.db）。
    """

    def __init__(self, feedback_db: str = "data/rag/.feedback.db"):
        self.feedback_db = feedback_db
        # 确保数据目录存在
        os.makedirs(os.path.dirname(feedback_db), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化反馈数据库。"""
        with sqlite3.connect(self.feedback_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_events (
                    search_id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    agent_id TEXT DEFAULT '',
                    task_id TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunk_feedback (
                    id TEXT PRIMARY KEY,
                    search_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    kb_name TEXT NOT NULL,
                    search_score REAL DEFAULT 0.0,
                    rerank_score REAL,
                    was_used INTEGER DEFAULT 0,
                    usage_quality INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (search_id) REFERENCES search_events(search_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_search_id
                ON chunk_feedback(search_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_kb_name
                ON chunk_feedback(kb_name)
            """)
            conn.commit()

    async def record_search(
        self,
        query: str,
        results: list[SearchResult],
        agent_id: str = "",
        task_id: str = "",
    ) -> str:
        """记录一次检索事件，返回 search_id 供后续关联。

        Args:
            query: 检索查询文本。
            results: 检索结果列表。
            agent_id: 发起检索的 Agent ID。
            task_id: 关联的任务 ID。

        Returns:
            本次检索的 search_id。
        """
        search_id = f"search_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        with sqlite3.connect(self.feedback_db) as conn:
            conn.execute(
                """INSERT INTO search_events (search_id, query, agent_id, task_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (search_id, query, agent_id, task_id, datetime.now().isoformat()),
            )

            # 为每个结果创建初始反馈记录（was_used=False）
            for result in results:
                feedback_id = f"fb_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """INSERT INTO chunk_feedback
                       (id, search_id, chunk_id, kb_name, search_score,
                        rerank_score, was_used, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
                    (
                        feedback_id, search_id, result.chunk_id,
                        result.kb_name, result.score,
                        result.rerank_score,
                        datetime.now().isoformat(),
                    ),
                )

            conn.commit()

        logger.debug("记录检索事件: search_id=%s, results=%d", search_id, len(results))
        return search_id

    async def record_usage(
        self,
        search_id: str,
        chunk_id: str,
        was_used: bool,
        usage_quality: int | None = None,
    ) -> None:
        """记录特定 chunk 是否被 Agent 引用。

        Args:
            search_id: 检索事件 ID。
            chunk_id: chunk ID。
            was_used: Agent 是否实际引用/使用了该结果。
            usage_quality: 使用质量评分 (1-5)，可选。
        """
        with sqlite3.connect(self.feedback_db) as conn:
            conn.execute(
                """UPDATE chunk_feedback
                   SET was_used = ?, usage_quality = ?
                   WHERE search_id = ? AND chunk_id = ?""",
                (1 if was_used else 0, usage_quality, search_id, chunk_id),
            )
            conn.commit()

    async def record_usage_batch(
        self,
        search_id: str,
        useful_chunk_ids: list[str] | None = None,
        useless_chunk_ids: list[str] | None = None,
    ) -> int:
        """批量记录 chunk 使用情况。

        Args:
            search_id: 检索事件 ID。
            useful_chunk_ids: 被 Agent 引用的 chunk ID 列表。
            useless_chunk_ids: 被 Agent 确认为不相关的 chunk ID 列表。

        Returns:
            更新的记录数。
        """
        count = 0
        with sqlite3.connect(self.feedback_db) as conn:
            if useful_chunk_ids:
                for cid in useful_chunk_ids:
                    conn.execute(
                        """UPDATE chunk_feedback SET was_used = 1
                           WHERE search_id = ? AND chunk_id = ?""",
                        (search_id, cid),
                    )
                    count += 1

            if useless_chunk_ids:
                for cid in useless_chunk_ids:
                    conn.execute(
                        """UPDATE chunk_feedback SET was_used = 0
                           WHERE search_id = ? AND chunk_id = ?""",
                        (search_id, cid),
                    )
                    count += 1

            conn.commit()
        return count

    async def get_feedback_stats(
        self,
        kb_name: str,
        time_range_days: int = 30,
    ) -> FeedbackStats:
        """获取检索反馈统计。

        Args:
            kb_name: 知识库名称。
            time_range_days: 统计时间范围（天），默认 30。

        Returns:
            FeedbackStats 统计结果。
        """
        cutoff = (datetime.now() - timedelta(days=time_range_days)).isoformat()

        with sqlite3.connect(self.feedback_db) as conn:
            # 总检索次数
            total = conn.execute(
                """SELECT COUNT(*) FROM search_events
                   WHERE created_at >= ?""",
                (cutoff,),
            ).fetchone()[0]

            # 总返回 chunk 数
            total_chunks = conn.execute(
                """SELECT COUNT(*) FROM chunk_feedback f
                   JOIN search_events e ON f.search_id = e.search_id
                   WHERE f.kb_name = ? AND e.created_at >= ?""",
                (kb_name, cutoff),
            ).fetchone()[0]

            # 被使用的 chunk 数
            used = conn.execute(
                """SELECT COUNT(*) FROM chunk_feedback f
                   JOIN search_events e ON f.search_id = e.search_id
                   WHERE f.kb_name = ? AND e.created_at >= ? AND f.was_used = 1""",
                (kb_name, cutoff),
            ).fetchone()[0]

            # 平均得分
            avg_used = conn.execute(
                """SELECT AVG(f.search_score) FROM chunk_feedback f
                   JOIN search_events e ON f.search_id = e.search_id
                   WHERE f.kb_name = ? AND e.created_at >= ? AND f.was_used = 1""",
                (kb_name, cutoff),
            ).fetchone()[0] or 0.0

            avg_unused = conn.execute(
                """SELECT AVG(f.search_score) FROM chunk_feedback f
                   JOIN search_events e ON f.search_id = e.search_id
                   WHERE f.kb_name = ? AND e.created_at >= ? AND f.was_used = 0""",
                (kb_name, cutoff),
            ).fetchone()[0] or 0.0

            # 低质量 chunks（高曝光低使用：出现 5 次以上但从未被使用）
            low_quality_rows = conn.execute(
                """SELECT f.chunk_id, COUNT(*) as cnt FROM chunk_feedback f
                   JOIN search_events e ON f.search_id = e.search_id
                   WHERE f.kb_name = ? AND e.created_at >= ?
                   GROUP BY f.chunk_id
                   HAVING SUM(f.was_used) = 0 AND cnt >= 5
                   ORDER BY cnt DESC LIMIT 20""",
                (kb_name, cutoff),
            ).fetchall()

        usage_rate = used / total_chunks if total_chunks > 0 else 0.0

        return FeedbackStats(
            total_searches=total,
            total_chunks_returned=total_chunks,
            chunks_used=used,
            usage_rate=usage_rate,
            avg_used_score=avg_used,
            avg_unused_score=avg_unused,
            top_queries=[],  # 可从 search_events 聚合
            low_quality_chunks=[row[0] for row in low_quality_rows],
        )
