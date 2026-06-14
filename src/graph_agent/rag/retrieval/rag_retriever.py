"""RAG 检索器。

编排查询预处理、关键词检索、向量检索、融合、重排序的完整流程。
支持多跳检索、上下文扩展和向量库降级。
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from graph_agent.rag.document_store import DocumentStore
from graph_agent.rag.rag_types import (
    Chunk,
    FusionMethod,
    MultiHopResult,
    SearchRequest,
    SearchResult,
)
from graph_agent.rag.retrieval.feedback_collector import FeedbackCollector
from graph_agent.rag.retrieval.fusion_engine import FusionEngine
from graph_agent.rag.retrieval.keyword_searcher import KeywordSearcher
from graph_agent.rag.retrieval.query_processor import QueryProcessor
from graph_agent.rag.retrieval.reranker import Reranker
from graph_agent.rag.retrieval.retrieval_cache import RetrievalCache
from graph_agent.rag.retrieval.vector_searcher import VectorSearcher
from graph_agent.rag.vector_store.base_vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


class VectorStoreUnavailableError(Exception):
    """向量存储不可用异常。"""
    pass


class RAGRetriever:
    """RAG 检索器。

    编排查询预处理、关键词检索、向量检索、融合、重排序的完整流程。
    支持多跳检索、上下文扩展和向量库降级。
    """

    def __init__(
        self,
        keyword_searcher: KeywordSearcher,
        vector_searcher: VectorSearcher,
        fusion_engine: FusionEngine,
        reranker: Reranker,
        document_store: DocumentStore,
        query_processor: QueryProcessor | None = None,
        feedback_collector: FeedbackCollector | None = None,
        retrieval_cache: RetrievalCache | None = None,
    ):
        self.keyword_searcher = keyword_searcher
        self.vector_searcher = vector_searcher
        self.fusion_engine = fusion_engine
        self.reranker = reranker
        self.document_store = document_store
        self.query_processor = query_processor
        self.feedback_collector = feedback_collector
        self.cache = retrieval_cache or RetrievalCache()
        self._degraded_mode = False

    async def retrieve(
        self,
        request: SearchRequest,
        enable_query_rewrite: bool = True,
        enable_multi_hop: bool = False,
        max_hops: int = 3,
    ) -> tuple[str, list[SearchResult]]:
        """执行完整的 RAG 检索流程。

        流程：
        0. 可选：查询预处理（改写/分解/HyDE）
        1. 并行执行关键词检索和向量检索
        2. 融合结果（加权求和或 RRF）
        3. 可选：重排序
        4. 可选：上下文扩展（加载相邻分块）
        5. 格式化返回 SearchResult 列表

        Args:
            request: 检索请求。
            enable_query_rewrite: 是否启用查询改写预处理。
            enable_multi_hop: 是否启用多跳检索。
            max_hops: 多跳检索的最大跳数。

        Returns:
            (search_id, SearchResult 列表)。
        """
        search_id = f"search_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # 检查缓存
        kb_names = request.kb_names or ["default"]
        if not enable_multi_hop:
            cached = self.cache.get(
                request.query, kb_names, request.top_k, request.metadata_filter
            )
            if cached is not None:
                return search_id, cached

        # 步骤 0：查询预处理
        query = request.query
        if enable_query_rewrite and self.query_processor:
            processed = await self.query_processor.process(query, strategies=["rewrite"])
            query = processed.rewritten or query

        # 步骤 1：并行检索
        keyword_results: list[tuple[Chunk, float]] = []
        vector_results: list[tuple[Chunk, float]] = []

        # 关键词检索
        for kb_name in kb_names:
            kw_results = self.keyword_searcher.search(
                kb_name=kb_name,
                query=query,
                top_k=request.rerank_top_k,
                metadata_filter=request.metadata_filter,
            )
            keyword_results.extend(kw_results)

        # 向量检索（带降级）
        try:
            for kb_name in kb_names:
                vec_raw = await self.vector_searcher.search(
                    kb_name=kb_name,
                    query=query,
                    top_k=request.rerank_top_k,
                    filter=request.metadata_filter,
                )
                # 重建 Chunk 对象
                for chunk_id, score, metadata in vec_raw:
                    chunk = self.document_store.get_chunk(chunk_id)
                    if chunk:
                        vector_results.append((chunk, score))
        except VectorStoreUnavailableError as e:
            logger.warning("向量检索不可用，降级为纯关键词检索: %s", e)
            self._degraded_mode = True
        except Exception as e:
            logger.warning("向量检索失败，降级为纯关键词检索: %s", e)
            self._degraded_mode = True

        # 步骤 2：融合
        if vector_results:
            fused = self.fusion_engine.fuse(
                keyword_results=keyword_results,
                vector_results=vector_results,
                method=request.fusion_method,
                keyword_weight=request.keyword_weight,
                vector_weight=request.vector_weight,
                top_k=request.rerank_top_k,
            )
        else:
            # 降级模式：仅关键词
            fused = keyword_results[:request.rerank_top_k]

        # 过滤低于阈值的结果
        fused = [
            (chunk, score) for chunk, score in fused
            if score >= request.similarity_threshold
        ]

        # 步骤 3：重排序
        if request.enable_rerank and len(fused) > request.top_k:
            chunks = [chunk for chunk, _ in fused]
            ranked = await self.reranker.rerank(
                query=query, chunks=chunks, top_k=request.top_k
            )
            # 合并 rerank 分数和原始分数
            final_results = self._combine_scores(fused, ranked)
        else:
            final_results = fused[:request.top_k]

        # 步骤 4：上下文扩展
        if request.include_adjacent:
            final_results = await self._expand_contexts(final_results, kb_names[0])

        # 步骤 5：格式化为 SearchResult
        search_results = self._format_results(final_results)

        # 记录反馈事件
        if self.feedback_collector:
            await self.feedback_collector.record_search(
                query=request.query,
                results=search_results,
            )

        # 写入缓存
        if not enable_multi_hop:
            self.cache.put(request.query, kb_names, request.top_k, search_results, request.metadata_filter)

        return search_id, search_results

    async def retrieve_multi_hop(
        self,
        query: str,
        kb_names: list[str],
        max_hops: int = 3,
        top_k_per_hop: int = 5,
    ) -> MultiHopResult:
        """执行多跳检索。"""
        from graph_agent.rag.rag_types import HopResult

        hops = []
        accumulated_chunks: dict[str, SearchResult] = {}
        current_query = query
        sufficiency = 0.0

        for hop_idx in range(1, max_hops + 1):
            request = SearchRequest(
                query=current_query,
                kb_names=kb_names,
                top_k=top_k_per_hop,
                enable_rerank=True,
            )
            _, results = await self.retrieve(request, enable_query_rewrite=(hop_idx == 1))

            # 记录新增信息
            new_ids = []
            for r in results:
                if r.chunk_id not in accumulated_chunks:
                    accumulated_chunks[r.chunk_id] = r
                    new_ids.append(r.chunk_id)

            hop_result = HopResult(
                hop_index=hop_idx,
                query=current_query,
                results=list(results),
                new_information=new_ids,
            )
            hops.append(hop_result)

            # 评估充分性
            if hop_idx < max_hops:
                sufficiency = await self._evaluate_sufficiency(
                    query, list(accumulated_chunks.values())
                )
                if sufficiency >= 0.9:
                    break
                # 生成下一跳查询
                current_query = await self._generate_next_query(
                    query,
                    [self.document_store.get_chunk(cid) for cid in accumulated_chunks],
                )

        return MultiHopResult(
            final_chunks=list(accumulated_chunks.values()),
            hops=hops,
            total_hops=len(hops),
            sufficiency_score=sufficiency,
        )

    async def _evaluate_sufficiency(
        self, query: str, results: list[SearchResult]
    ) -> float:
        """LLM 评估当前检索结果是否足够回答原始问题。"""
        if not self.query_processor or not self.query_processor.llm:
            return 0.5

        content_summary = "\n".join(
            r.content[:200] for r in results[:5]
        )
        prompt = f"""评估以下检索结果是否足够回答查询（0-1之间的数值）。

查询: {query}

检索结果摘要:
{content_summary}

只返回一个 0-1 之间的数值，表示信息充分程度。"""

        try:
            response = await self.query_processor.llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            score = float(content.strip()[:10])
            return max(0.0, min(1.0, score))
        except Exception:
            return 0.5

    async def _generate_next_query(
        self,
        original_query: str,
        accumulated_chunks: list[Chunk],
    ) -> str:
        """基于已有信息生成下一跳的检索查询。"""
        if not self.query_processor or not self.query_processor.llm:
            return original_query

        valid_chunks = [c for c in accumulated_chunks if c is not None]
        accumulated_content = "\n".join(
            c.content[:300] for c in valid_chunks[:5]
        )

        prompt = f"""原始问题: {original_query}

已检索到的信息:
{accumulated_content}

为了完整回答原始问题，还需要了解什么信息？
请生成一个简短的检索查询来补充缺失的信息。
只返回检索查询文本。"""

        try:
            response = await self.query_processor.llm.ainvoke(prompt)
            return response.content.strip() if hasattr(response, "content") else str(response).strip()
        except Exception:
            return original_query

    async def _expand_contexts(
        self,
        results: list[tuple[Chunk, float]],
        kb_name: str,
    ) -> list[tuple[Chunk, float]]:
        """上下文扩展：加载相邻分块。"""
        expanded = []
        for chunk, score in results:
            expanded.append((chunk, score))

            # 加载 prev chunk
            prev_id = chunk.metadata.get("prev_chunk_id", "")
            if prev_id:
                prev_chunk = self.document_store.get_chunk(prev_id)
                if prev_chunk:
                    expanded.append((prev_chunk, score * 0.8))

            # 加载 next chunk
            next_id = chunk.metadata.get("next_chunk_id", "")
            if next_id:
                next_chunk = self.document_store.get_chunk(next_id)
                if next_chunk:
                    expanded.append((next_chunk, score * 0.8))

        return expanded

    def _combine_scores(
        self,
        original: list[tuple[Chunk, float]],
        ranked: list[tuple[Chunk, float]],
    ) -> list[tuple[Chunk, float]]:
        """合并原始检索分数与 LLM 重排序分数。

        final_score = 0.3 * original_score + 0.7 * rerank_score
        """
        orig_map = {chunk.id: score for chunk, score in original}
        combined = []
        for chunk, rerank_score in ranked:
            orig_score = orig_map.get(chunk.id, 0.5)
            final = 0.3 * orig_score + 0.7 * rerank_score
            combined.append((chunk, final))
        combined.sort(key=lambda x: x[1], reverse=True)
        return combined

    def _format_results(
        self,
        scored_chunks: list[tuple[Chunk, float]],
    ) -> list[SearchResult]:
        """将 (Chunk, score) 列表格式化为 SearchResult 列表。"""
        results = []
        for chunk, score in scored_chunks:
            results.append(SearchResult(
                chunk_id=chunk.id,
                doc_id=chunk.doc_id,
                kb_name=chunk.kb_name,
                content=chunk.content,
                score=score,
                source_file=chunk.metadata.get("source_file", ""),
                metadata=chunk.metadata,
            ))
        return results

    @property
    def is_degraded(self) -> bool:
        """当前是否处于降级模式（仅关键词检索）。"""
        return self._degraded_mode
