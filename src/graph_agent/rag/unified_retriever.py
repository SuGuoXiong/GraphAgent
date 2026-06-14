"""统一检索器。

同时检索记忆系统（对话提取的用户偏好、架构决策等）和知识库文档。
通过 RRF 融合跨源结果，Agent 无需区分信息来自哪里。
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from graph_agent.rag.rag_types import SearchRequest, SearchResult
from graph_agent.rag.retrieval.fusion_engine import FusionEngine

logger = logging.getLogger(__name__)


class UnifiedSearchResponse:
    """统一检索响应。"""

    def __init__(
        self,
        search_id: str = "",
        memory_results: list | None = None,
        document_results: list[SearchResult] | None = None,
        merged_summary: str = "",
    ):
        self.search_id = search_id
        self.memory_results = memory_results or []
        self.document_results = document_results or []
        self.merged_summary = merged_summary

    def to_dict(self) -> dict:
        result = {
            "search_id": self.search_id,
            "memory_results": [r.__dict__ if hasattr(r, "__dict__") else r for r in self.memory_results],
            "document_results": [
                {
                    "chunk_id": r.chunk_id,
                    "doc_id": r.doc_id,
                    "kb_name": r.kb_name,
                    "content": r.content,
                    "score": r.score,
                    "source_file": r.source_file,
                    "metadata": r.metadata,
                }
                for r in self.document_results
            ],
        }
        if self.merged_summary:
            result["merged_summary"] = self.merged_summary
        return result


class UnifiedRetriever:
    """统一检索器。

    支持多种检索模式：
    - 标准检索（memory + document 关键词+向量）
    - 扩展检索（未来可接入 GraphRAG 知识图谱检索）

    侧重于跨多个数据源的联合检索。
    """

    def __init__(
        self,
        memory_searcher: Any,  # MemorySearcher from graph_agent.memory
        rag_retriever: Any,     # RAGRetriever
        llm: Any = None,
    ):
        self.memory_searcher = memory_searcher
        self.rag_retriever = rag_retriever
        self.llm = llm
        self.fusion_engine = FusionEngine()

    async def search(
        self,
        query: str,
        sources: list[str] | None = None,
        memory_search_params: dict | None = None,
        document_search_params: dict | None = None,
        merge: bool = True,
        strategy: str = "parallel",
    ) -> UnifiedSearchResponse:
        """统一检索。

        检索策略：
        1. Parallel（默认）: 并行检索两种数据源，通过 RRF 合并结果。
        2. Cascaded: 先在 memory 中检索，不足时再检索 documents。
        3. Selective: LLM 分析查询后决定检索哪个数据源。

        Args:
            query: 查询文本。
            sources: 检索来源列表。可选值: "memory", "documents"。默认两者都检索。
            memory_search_params: 记忆检索參數。
            document_search_params: 文档检索参数。
            merge: 是否合并去重不同来源的结果（RRF）。
            strategy: 检索策略。

        Returns:
            UnifiedSearchResponse。
        """
        search_id = f"unified_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        if sources is None:
            sources = ["memory", "documents"]

        # 策略选择
        if strategy == "selective" and self.llm:
            sources = await self._selective_sources(query, sources)
        elif strategy == "cascaded":
            return await self._cascaded_search(
                query, sources, memory_search_params,
                document_search_params, merge, search_id,
            )

        # 默认：Parallel
        memory_results = []
        document_results = []

        if "memory" in sources:
            memory_results = await self._search_memory(
                query, **(memory_search_params or {})
            )

        if "documents" in sources:
            doc_params = document_search_params or {}
            doc_request = SearchRequest(
                query=query,
                top_k=doc_params.get("top_k", 5),
                enable_rerank=doc_params.get("enable_rerank", True),
                include_adjacent=doc_params.get("include_adjacent", False),
                metadata_filter=doc_params.get("filter"),
            )
            if doc_params.get("kb_names"):
                doc_request.kb_names = doc_params["kb_names"]

            _, document_results = await self.rag_retriever.retrieve(doc_request)

        return UnifiedSearchResponse(
            search_id=search_id,
            memory_results=memory_results,
            document_results=document_results,
        )

    async def _search_memory(self, query: str, **params) -> list:
        """调用记忆检索。"""
        if self.memory_searcher is None:
            return []

        try:
            from graph_agent.memory.memory_types import SearchMemoryInput

            input_ = SearchMemoryInput(
                query=query,
                limit=params.get("top_k", 5),
                include_archived=params.get("include_archived_memories", False),
                type=params.get("type"),
                scope=params.get("scope"),
            )
            results = self.memory_searcher.search(input_)
            return results if isinstance(results, list) else [results]
        except Exception as e:
            logger.error("记忆检索失败: %s", e)
            return []

    async def _cascaded_search(
        self,
        query: str,
        sources: list[str],
        memory_params: dict | None,
        document_params: dict | None,
        merge: bool,
        search_id: str,
    ) -> UnifiedSearchResponse:
        """级联检索：先 memory，不足时自动补充 documents。"""
        memory_results = []
        document_results = []

        if "memory" in sources:
            memory_results = await self._search_memory(
                query, **(memory_params or {})
            )

        # 判断充分性：如果 memory 返回结果少于 3 条，补充文档检索
        if "documents" in sources and len(memory_results) < 3:
            doc_params = document_params or {}
            doc_request = SearchRequest(
                query=query,
                top_k=doc_params.get("top_k", 5),
                enable_rerank=doc_params.get("enable_rerank", True),
            )
            if doc_params.get("kb_names"):
                doc_request.kb_names = doc_params["kb_names"]
            _, document_results = await self.rag_retriever.retrieve(doc_request)

        return UnifiedSearchResponse(
            search_id=search_id,
            memory_results=memory_results,
            document_results=document_results,
        )

    async def _selective_sources(
        self, query: str, available_sources: list[str]
    ) -> list[str]:
        """LLM 分析查询后决定检索哪个数据源。"""
        if self.llm is None:
            return available_sources

        prompt = f"""分析以下查询，判断需要从哪些数据源检索信息。
数据源: memory（用户偏好、历史决策、对话记忆）、documents（项目文档、API参考、技术规范）

查询: {query}

返回 JSON 数组: ["memory"] 或 ["documents"] 或 ["memory", "documents"]
只返回 JSON 数组。"""

        try:
            import json
            import re
            response = await self.llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            match = re.search(r'\[.*\]', content)
            if match:
                selected = json.loads(match.group())
                selected = [s for s in selected if s in available_sources]
                if selected:
                    return selected
        except Exception as e:
            logger.error("选择性检索策略失败: %s", e)

        return available_sources

    async def generate_summary(
        self, query: str, results: list[SearchResult]
    ) -> str:
        """使用 LLM 生成检索结果的综合摘要。"""
        if self.llm is None:
            return ""

        chunks_text = "\n---\n".join(
            f"[来源: {r.source_file}] {r.content[:300]}"
            for r in results[:5]
        )

        prompt = f"""基于以下检索结果，生成查询的综合摘要（200字以内）。

查询: {query}

检索结果:
{chunks_text}

综合摘要:"""

        try:
            response = await self.llm.ainvoke(prompt)
            return response.content.strip() if hasattr(response, "content") else str(response).strip()
        except Exception as e:
            logger.error("生成综合摘要失败: %s", e)
            return ""
