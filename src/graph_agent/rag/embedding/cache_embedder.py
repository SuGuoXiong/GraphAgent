"""缓存装饰嵌入器。

装饰器模式：包裹一个 BaseEmbedder 实例，对嵌入结果进行持久化缓存。
以内容的 SHA256 哈希作为缓存键，天然实现跨文档的分块级嵌入去重。
"""

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path

from .base_embedder import BaseEmbedder

logger = logging.getLogger(__name__)


class CacheEmbedder(BaseEmbedder):
    """缓存装饰嵌入器。

    包裹任意 BaseEmbedder 实现，在调用底层嵌入前先查询缓存。
    若缓存命中则直接返回，否则调用底层嵌入器并将结果写入缓存。

    特性：
    - SHA256 内容哈希作为缓存键，自动去重
    - SQLite 持久化存储（data/rag/.embed_cache.db）
    - 跨文档、跨知识库的分块级去重
    - 区分模型：不同模型的结果分开缓存
    """

    def __init__(self, embedder: BaseEmbedder,
                 cache_db: str = "data/rag/.embed_cache.db"):
        """初始化缓存嵌入器。

        Args:
            embedder: 被装饰的底层嵌入器实例
            cache_db: SQLite 缓存数据库路径
        """
        super().__init__(embedder.model_name)
        self._embedder = embedder
        self._cache_db_path = Path(cache_db)
        self._cache_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化缓存数据库表结构。"""
        with sqlite3.connect(str(self._cache_db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embed_cache (
                    content_hash TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    created_at REAL NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    PRIMARY KEY (content_hash, model_name)
                )
            """)
            # 查询索引
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_embed_cache_model
                ON embed_cache(model_name)
            """)
            # 最近使用索引（用于缓存淘汰）
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_embed_cache_created
                ON embed_cache(created_at)
            """)
            conn.commit()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本批次进行嵌入（带缓存）。

        对每条文本计算 SHA256 哈希后查询缓存：
        - 命中：直接返回缓存的嵌入向量
        - 未命中：调用底层嵌入器，结果写入缓存

        Args:
            texts: 待嵌入的文本列表

        Returns:
            嵌入向量列表，顺序与 texts 一致
        """
        if not texts:
            return []

        # 分类：已缓存的和待嵌入的
        cached_embeddings: dict[int, list[float]] = {}  # index -> embedding
        uncached_texts: dict[int, str] = {}  # index -> text

        for i, text in enumerate(texts):
            content_hash = self._compute_hash(text)
            cached = self._get_cache(content_hash)
            if cached is not None:
                cached_embeddings[i] = cached
            else:
                uncached_texts[i] = text

        cache_hits = len(cached_embeddings)
        cache_misses = len(uncached_texts)

        if cache_hits > 0:
            logger.debug(
                "嵌入缓存命中: %d/%d 条 (命中率 %.1f%%)",
                cache_hits, len(texts),
                (cache_hits / len(texts) * 100) if texts else 0,
            )

        # 对未缓存的文本调用底层嵌入器
        if uncached_texts:
            uncached_indices = sorted(uncached_texts.keys())
            uncached_text_list = [uncached_texts[i] for i in uncached_indices]

            logger.debug("调用底层嵌入器处理 %d 条未缓存文本", len(uncached_text_list))
            new_embeddings = self._embedder.embed(uncached_text_list)

            # 写入缓存
            for idx, embedding in zip(uncached_indices, new_embeddings):
                text = uncached_texts[idx]
                content_hash = self._compute_hash(text)
                self._set_cache(content_hash, embedding)
                cached_embeddings[idx] = embedding

        # 按原始顺序重建结果
        return [
            cached_embeddings[i]
            for i in range(len(texts))
        ]

    def embed_query(self, query: str) -> list[float]:
        """对单个查询文本进行嵌入（带缓存）。

        查询缓存命中率通常较低，但仍提供缓存以应对重复查询场景。

        Args:
            query: 查询文本

        Returns:
            查询嵌入向量
        """
        content_hash = self._compute_hash(query)
        cached = self._get_cache(content_hash)
        if cached is not None:
            logger.debug("查询嵌入缓存命中")
            return cached

        result = self._embedder.embed_query(query)
        self._set_cache(content_hash, result)
        return result

    def get_dimension(self) -> int:
        """获取嵌入向量维度（委托给底层嵌入器）。"""
        return self._embedder.get_dimension()

    def get_batch_size(self) -> int:
        """获取批处理大小（委托给底层嵌入器）。"""
        return self._embedder.get_batch_size()

    def get_max_input_tokens(self) -> int:
        """获取最大输入 token 数（委托给底层嵌入器）。"""
        return self._embedder.get_max_input_tokens()

    # ------------------------------------------------------------------
    # 缓存统计
    # ------------------------------------------------------------------

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息。

        Returns:
            包含 total_entries、total_hits、hit_rate 等字段的字典
        """
        with sqlite3.connect(str(self._cache_db_path)) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM embed_cache WHERE model_name = ?",
                (self.model_name,),
            ).fetchone()[0]

            total_hits = conn.execute(
                "SELECT COALESCE(SUM(hit_count), 0) FROM embed_cache WHERE model_name = ?",
                (self.model_name,),
            ).fetchone()[0]

        return {
            "model_name": self.model_name,
            "total_entries": total,
            "total_hits": total_hits,
            "cache_db_path": str(self._cache_db_path),
        }

    def clear_cache(self, model_name: str | None = None) -> int:
        """清除缓存。

        Args:
            model_name: 指定模型名，None 时清除所有模型的缓存

        Returns:
            删除的条目数
        """
        target_model = model_name or self.model_name
        with sqlite3.connect(str(self._cache_db_path)) as conn:
            if model_name is None:
                count = conn.execute(
                    "SELECT COUNT(*) FROM embed_cache"
                ).fetchone()[0]
                conn.execute("DELETE FROM embed_cache")
            else:
                count = conn.execute(
                    "SELECT COUNT(*) FROM embed_cache WHERE model_name = ?",
                    (target_model,),
                ).fetchone()[0]
                conn.execute(
                    "DELETE FROM embed_cache WHERE model_name = ?",
                    (target_model,),
                )
            conn.commit()

        logger.info("已清除 %s 模型的 %d 条嵌入缓存", target_model, count)
        return count

    def prune_cache(self, max_entries: int = 100_000) -> int:
        """淘汰最旧的缓存条目，将总条目数控制在 max_entries 以内。

        Args:
            max_entries: 保留的最大条目数

        Returns:
            删除的条目数
        """
        with sqlite3.connect(str(self._cache_db_path)) as conn:
            current = conn.execute(
                "SELECT COUNT(*) FROM embed_cache WHERE model_name = ?",
                (self.model_name,),
            ).fetchone()[0]

            if current <= max_entries:
                return 0

            # 保留最新的 max_entries 条，删除其余
            delete_count = current - max_entries
            conn.execute("""
                DELETE FROM embed_cache
                WHERE model_name = ? AND rowid IN (
                    SELECT rowid FROM embed_cache
                    WHERE model_name = ?
                    ORDER BY created_at ASC
                    LIMIT ?
                )
            """, (self.model_name, self.model_name, delete_count))
            conn.commit()

        logger.info(
            "淘汰 %s 模型的 %d 条旧缓存（剩余 %d 条）",
            self.model_name, delete_count, max_entries,
        )
        return delete_count

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_hash(text: str) -> str:
        """计算文本的 SHA256 哈希。"""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _get_cache(self, content_hash: str) -> list[float] | None:
        """从缓存中查找嵌入向量。

        Args:
            content_hash: 内容 SHA256 哈希

        Returns:
            嵌入向量，未命中时返回 None
        """
        with sqlite3.connect(str(self._cache_db_path)) as conn:
            row = conn.execute(
                """SELECT embedding_json FROM embed_cache
                   WHERE content_hash = ? AND model_name = ?""",
                (content_hash, self.model_name),
            ).fetchone()

            if row is None:
                return None

            # 更新命中次数
            conn.execute(
                """UPDATE embed_cache SET hit_count = hit_count + 1
                   WHERE content_hash = ? AND model_name = ?""",
                (content_hash, self.model_name),
            )
            conn.commit()

            return json.loads(row[0])

    def _set_cache(self, content_hash: str, embedding: list[float]) -> None:
        """将嵌入向量写入缓存。

        Args:
            content_hash: 内容 SHA256 哈希
            embedding: 嵌入向量
        """
        with sqlite3.connect(str(self._cache_db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO embed_cache
                   (content_hash, model_name, embedding_json, dimension,
                    source, created_at, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (
                    content_hash,
                    self.model_name,
                    json.dumps(embedding),
                    len(embedding),
                    "cache_embedder",
                    time.time(),
                ),
            )
            conn.commit()
