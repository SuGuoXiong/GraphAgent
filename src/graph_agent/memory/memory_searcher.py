"""记忆检索层 —— 封装 FTS5 检索逻辑，替代旧 memory_retriever.py。

提供:
- FTS5 全文检索（带降级路径）
- 检索结果自动更新 access 统计
- 归档记忆唤醒
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from graph_agent.memory.memory_types import (
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    MemoryScope,
    SearchMemoryInput,
    SearchMemoryResult,
    _now,
)

if TYPE_CHECKING:
    from graph_agent.memory.memory_indexer import MemoryIndexer
    from graph_agent.memory.memory_store import MemoryStore

logger = logging.getLogger(__name__)


class MemorySearcher:
    """记忆检索器 —— 封装 FTS5 检索和降级逻辑。

    检索流程:
        1. FTS5 MATCH 全文检索
        2. 无结果 → 降级 LIKE 模糊匹配
        3. 自动更新 access_at / access_count
        4. 触发归档记忆唤醒
    """

    def __init__(self, indexer: "MemoryIndexer", store: "MemoryStore"):
        self._indexer = indexer
        self._store = store

    def search(self, input_: SearchMemoryInput) -> list[SearchMemoryResult]:
        """执行记忆检索。

        Args:
            input_: 检索参数（query, type, scope, include_archived, limit）

        Returns:
            按相关度排序的 SearchMemoryResult 列表
        """
        # 构造状态过滤器
        if input_.include_archived:
            status_filter = (MemoryStatus.ACTIVE.value, MemoryStatus.ARCHIVED.value)
        else:
            status_filter = (MemoryStatus.ACTIVE.value,)

        # 构造 FTS5 查询表达式
        query_pattern = self._build_fts_query(input_.query)

        # 尝试 FTS5 检索
        results = self._indexer.search_fts(
            query_pattern=query_pattern,
            status_filter=status_filter,
            type_filter=input_.type,
            scope_filter=input_.scope,
            limit=input_.limit,
        )

        # 无结果 → 降级 LIKE 检索
        if not results:
            results = self._indexer.search_fallback(
                keyword=input_.query.strip(),
                status_filter=status_filter,
                type_filter=input_.type,
                scope_filter=input_.scope,
                limit=input_.limit,
            )

        # 转换为 SearchMemoryResult
        search_results: list[SearchMemoryResult] = []
        for r in results:
            sr = SearchMemoryResult(
                id=r["id"],
                type=r["type"],
                scope=r["scope"],
                status=r["status"],
                tags=self._parse_tags(r.get("tags", "")),
                content=r.get("content", ""),
                created_at=r.get("created_at", ""),
                updated_at=r.get("updated_at", ""),
                relevance=r.get("relevance", 0.0),
            )
            search_results.append(sr)

            # 更新访问统计（仅更新 DB）
            self._indexer.touch_access(r["id"])

            # 唤醒机制：ARCHIVED → ACTIVE
            if r["status"] == MemoryStatus.ARCHIVED.value:
                self._wake_up(r["id"], r["scope"], r["id"][:10])

        return search_results

    def _build_fts_query(self, query: str) -> str:
        """将用户查询构造为 FTS5 MATCH 表达式。

        转义 FTS5 特殊字符，多关键词用 OR 连接。
        """
        # 转义 FTS5 特殊字符
        escaped = re.sub(r'(["*()])', r'\\\1', query.strip())
        if not escaped:
            return '""'

        # 按空白分割关键词
        keywords = escaped.split()
        if len(keywords) == 1:
            return f'"{keywords[0]}"'
        else:
            # 多关键词 OR 连接
            parts = [f'"{kw}"' for kw in keywords if kw]
            return " OR ".join(parts) if parts else f'"{escaped}"'

    def _wake_up(self, memory_id: str, scope: str, created_date: str) -> None:
        """唤醒归档记忆：ARCHIVED → ACTIVE，从 archive/ 移回原文件。"""
        try:
            # 更新状态
            self._indexer.update_meta(
                memory_id,
                status=MemoryStatus.ACTIVE.value,
                updated_at=_now(),
                file_path=f"{scope}/{created_date}.md",
            )

            # 移动文件区块
            self._store.move_record_between_files(
                memory_id=memory_id,
                from_scope="archive",
                from_date=created_date,
                to_scope=scope,
                to_date=created_date,
            )

            logger.info(f"记忆已唤醒: {memory_id}")
        except Exception as e:
            logger.warning(f"唤醒记忆失败 {memory_id}: {e}")

    @staticmethod
    def _parse_tags(tags_raw: str) -> list[str]:
        """解析 tags 字段（JSON array 字符串 → list）。"""
        if not tags_raw:
            return []
        try:
            import json
            tags = json.loads(tags_raw)
            return tags if isinstance(tags, list) else []
        except (json.JSONDecodeError, TypeError):
            # 回退: 逗号分隔字符串
            return [t.strip() for t in tags_raw.split(",") if t.strip()]
