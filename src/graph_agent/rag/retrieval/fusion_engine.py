"""检索结果融合引擎。

支持两种融合策略：
1. Weighted Sum（加权求和）— 分别归一化后加权合并
2. RRF（倒数排名融合）— 无需分数归一化，天然跨源可比
"""

import logging
from math import log

from graph_agent.rag.rag_types import Chunk, FusionMethod

logger = logging.getLogger(__name__)

# RRF 常数 k，用于平滑排名差异
RRF_K = 60


class FusionEngine:
    """检索结果融合引擎。"""

    def fuse(
        self,
        keyword_results: list[tuple[Chunk, float]],
        vector_results: list[tuple[Chunk, float]],
        method: FusionMethod = FusionMethod.WEIGHTED_SUM,
        keyword_weight: float = 0.3,
        vector_weight: float = 0.7,
        top_k: int = 20,
    ) -> list[tuple[Chunk, float]]:
        """融合关键词和向量检索结果。

        Args:
            keyword_results: 关键词检索结果 (chunk, score)。
            vector_results: 向量检索结果 (chunk, score)。
            method: 融合方法。
            keyword_weight: 关键词权重（0-1）。
            vector_weight: 向量权重（0-1），与 keyword_weight 之和应为 1。
            top_k: 返回的最大结果数。

        Returns:
            融合后的 (chunk, fused_score) 列表，按 fused_score 降序。
        """
        if method == FusionMethod.WEIGHTED_SUM:
            return self._weighted_sum_fuse(
                keyword_results, vector_results,
                keyword_weight, vector_weight, top_k,
            )
        elif method == FusionMethod.RRF:
            return self._rrf_fuse(
                keyword_results, vector_results, top_k,
            )
        else:
            logger.warning("未知融合方法 %s，使用 weighted_sum", method)
            return self._weighted_sum_fuse(
                keyword_results, vector_results,
                keyword_weight, vector_weight, top_k,
            )

    def _weighted_sum_fuse(
        self,
        keyword_results: list[tuple[Chunk, float]],
        vector_results: list[tuple[Chunk, float]],
        keyword_weight: float,
        vector_weight: float,
        top_k: int,
    ) -> list[tuple[Chunk, float]]:
        """Weighted Sum 融合方法。

        1. 分别对关键词分数和向量分数做 min-max 归一化到 [0, 1]
        2. 合并两个结果集（去重，按 chunk_id）
        3. final_score = kw_norm * keyword_weight + vec_norm * vector_weight
        4. 按 final_score 降序排序
        """
        # 归一化
        kw_norm = self._normalize_scores(keyword_results)
        vec_norm = self._normalize_scores(vector_results)

        # 合并
        scores: dict[str, tuple[Chunk, float]] = {}

        for chunk, score in kw_norm:
            cid = chunk.id
            scores[cid] = (chunk, score * keyword_weight)

        for chunk, score in vec_norm:
            cid = chunk.id
            if cid in scores:
                # 已存在：累加向量权重部分
                existing_chunk, existing_score = scores[cid]
                scores[cid] = (existing_chunk, existing_score + score * vector_weight)
            else:
                scores[cid] = (chunk, score * vector_weight)

        # 排序
        sorted_results = sorted(
            scores.values(), key=lambda x: x[1], reverse=True
        )

        return sorted_results[:top_k]

    def _rrf_fuse(
        self,
        keyword_results: list[tuple[Chunk, float]],
        vector_results: list[tuple[Chunk, float]],
        top_k: int,
    ) -> list[tuple[Chunk, float]]:
        """RRF（倒数排名融合）方法。

        1. 对每个结果，计算 RRF 分数：
           score = 1/(k + rank_kw) + 1/(k + rank_vec)
           其中 k=60（RRF 常数）
        2. 按 RRF 分数降序排序

        优势：不依赖分数归一化，天然跨源可比。
        """
        # 构建排名映射（按分数降序排列，rank 从 1 开始）
        kw_ranks: dict[str, int] = {}
        for rank, (chunk, _) in enumerate(keyword_results, start=1):
            kw_ranks[chunk.id] = rank

        vec_ranks: dict[str, int] = {}
        for rank, (chunk, _) in enumerate(vector_results, start=1):
            vec_ranks[chunk.id] = rank

        # 计算 RRF 分数
        all_chunk_ids = set(kw_ranks.keys()) | set(vec_ranks.keys())
        chunk_map: dict[str, Chunk] = {}

        # 构建 chunk_id -> Chunk 映射
        for chunk, _ in keyword_results:
            chunk_map[chunk.id] = chunk
        for chunk, _ in vector_results:
            chunk_map.setdefault(chunk.id, chunk)

        rrf_scores: list[tuple[Chunk, float]] = []
        for cid in all_chunk_ids:
            kw_rank = kw_ranks.get(cid, len(keyword_results) + 1)
            vec_rank = vec_ranks.get(cid, len(vector_results) + 1)
            rrf_score = 1.0 / (RRF_K + kw_rank) + 1.0 / (RRF_K + vec_rank)
            rrf_scores.append((chunk_map[cid], rrf_score))

        # 排序
        rrf_scores.sort(key=lambda x: x[1], reverse=True)
        return rrf_scores[:top_k]

    @staticmethod
    def _normalize_scores(
        results: list[tuple[Chunk, float]],
    ) -> list[tuple[Chunk, float]]:
        """Min-max 归一化到 [0, 1] 区间。

        如果所有分数相同或只有一条结果，全部归一化为 1.0。
        """
        if not results:
            return []

        if len(results) == 1:
            return [(results[0][0], 1.0)]

        scores = [s for _, s in results]
        min_score = min(scores)
        max_score = max(scores)

        if max_score == min_score:
            return [(chunk, 1.0) for chunk, _ in results]

        return [
            (chunk, (score - min_score) / (max_score - min_score))
            for chunk, score in results
        ]

    def cross_source_rrf(
        self,
        memory_results: list[tuple[any, float]],
        document_results: list[tuple[any, float]],
        top_k: int = 10,
    ) -> list[tuple[any, float]]:
        """跨源 RRF 融合（用于 UnifiedRetriever 合并 memory + document 结果）。

        Memory 的 BM25 分数和 Document 的融合分数不可直接比较，
        因此统一使用 RRF 进行跨源合并。

        Args:
            memory_results: 记忆检索结果 (item, score)。
            document_results: 文档检索结果 (item, score)。
            top_k: 返回的最大结果数。

        Returns:
            RRF 融合后的 (item, rrf_score) 列表。
        """
        # 构建各自源内的排名
        mem_ranks: dict[int, int] = {}
        mem_items: dict[int, any] = {}
        for rank, (item, _) in enumerate(memory_results, start=1):
            idx = id(item)
            mem_ranks[idx] = rank
            mem_items[idx] = item

        doc_ranks: dict[int, int] = {}
        doc_items: dict[int, any] = {}
        for rank, (item, _) in enumerate(document_results, start=1):
            idx = id(item)
            doc_ranks[idx] = rank
            doc_items[idx] = item

        # RRF
        all_ids = set(mem_ranks.keys()) | set(doc_ranks.keys())
        merged: list[tuple[any, float]] = []

        for idx in all_ids:
            mem_rank = mem_ranks.get(idx, len(memory_results) + 1)
            doc_rank = doc_ranks.get(idx, len(document_results) + 1)
            rrf_score = 1.0 / (RRF_K + mem_rank) + 1.0 / (RRF_K + doc_rank)
            item = mem_items.get(idx) or doc_items.get(idx)
            merged.append((item, rrf_score))

        merged.sort(key=lambda x: x[1], reverse=True)
        return merged[:top_k]
