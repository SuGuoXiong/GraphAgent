"""检索结果缓存（会话级 LRU）。

同一 Agent 会话中重复查询避免重复的 embedding API 调用和检索计算。
缓存按 (query_hash, kb_names_hash, top_k, filter_hash) 作为 key。
"""

import hashlib
import json
import logging
import time
from typing import Optional

from graph_agent.rag.rag_types import SearchResult

logger = logging.getLogger(__name__)


class RetrievalCache:
    """检索结果缓存（会话级 LRU）。

    仅缓存单跳检索（非 multi-hop）结果，因为多跳检索涉及 LLM 推理，
    每次执行结果可能不同。

    缓存失效：当知识库发生写入操作（ingest/delete/reindex）后，
    自动调用 invalidate(kb_name) 清除相关缓存。
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        """初始化缓存。

        Args:
            max_size: 最大缓存条目数，默认 100。
            ttl_seconds: 缓存有效期（秒），默认 300（5 分钟）。
                         与 Anthropic prompt cache TTL 对齐。
        """
        self._cache: dict[str, tuple[float, list[SearchResult]]] = {}
        self.max_size = max_size
        self.ttl = ttl_seconds

    def _make_key(
        self,
        query: str,
        kb_names: list[str],
        top_k: int,
        metadata_filter: dict | None = None,
    ) -> str:
        """生成缓存 key。

        使用 SHA256 哈希缩短 key 长度。
        """
        raw = json.dumps({
            "q": query.lower().strip(),
            "kb": sorted(kb_names),
            "k": top_k,
            "f": metadata_filter or {},
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def get(
        self,
        query: str,
        kb_names: list[str],
        top_k: int,
        metadata_filter: dict | None = None,
    ) -> list[SearchResult] | None:
        """查询缓存。

        Returns:
            命中返回结果列表，过期或未命中返回 None。
        """
        key = self._make_key(query, kb_names, top_k, metadata_filter)

        if key not in self._cache:
            return None

        timestamp, results = self._cache[key]
        if time.time() - timestamp > self.ttl:
            # 过期，删除
            del self._cache[key]
            logger.debug("缓存过期: key=%s", key)
            return None

        logger.debug("缓存命中: key=%s, results=%d", key, len(results))
        return results

    def put(
        self,
        query: str,
        kb_names: list[str],
        top_k: int,
        results: list[SearchResult],
        metadata_filter: dict | None = None,
    ) -> None:
        """写入缓存。

        超过 max_size 时淘汰最旧的条目（LRU）。
        """
        key = self._make_key(query, kb_names, top_k, metadata_filter)

        # LRU 淘汰
        if len(self._cache) >= self.max_size:
            oldest_key = min(
                self._cache.keys(),
                key=lambda k: self._cache[k][0],
            )
            del self._cache[oldest_key]
            logger.debug("缓存淘汰: key=%s", oldest_key)

        self._cache[key] = (time.time(), results)
        logger.debug("缓存写入: key=%s, results=%d", key, len(results))

    def invalidate(self, kb_name: str | None = None) -> int:
        """失效缓存。

        Args:
            kb_name: 指定知识库名称，清除涉及该 KB 的所有缓存。
                     None 则清空全部缓存。

        Returns:
            清除的缓存条目数。
        """
        if kb_name is None:
            count = len(self._cache)
            self._cache.clear()
            logger.debug("缓存全部清空: %d 条", count)
            return count

        # 指定 KB：需要根据 key 判断是否包含该 KB 名称
        # 由于 key 是哈希值，无法反向判断，这里采用保守策略：
        # 如果缓存条目涉及的所有 KB 中包含指定 KB，则清除
        # 实际上由于 key 是哈希，我们采用更简单的策略：清空全部
        #
        # 优化方案：维护 kb_name -> keys 的索引
        # 当前简化实现：仅支持全局或全部清除
        count = len(self._cache)
        self._cache.clear()
        logger.debug("缓存失效 (kb=%s): %d 条", kb_name, count)
        return count

    def size(self) -> int:
        """当前缓存条目数。"""
        return len(self._cache)

    def clear(self) -> None:
        """清空缓存。"""
        self._cache.clear()
