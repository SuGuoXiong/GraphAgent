"""向量语义检索器。

基于向量相似度检索文档片段，调用嵌入服务生成查询向量，
然后通过向量库检索最相似的 chunk。
"""

import logging
from typing import Optional

from graph_agent.rag.rag_types import Chunk
from graph_agent.rag.vector_store.base_vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


class VectorSearcher:
    """基于向量相似度的检索器。"""

    def __init__(self, vector_store: BaseVectorStore, embedder: "BaseEmbedder"):
        """初始化向量检索器。

        Args:
            vector_store: 向量存储后端。
            embedder: 嵌入服务（用于生成查询向量）。
        """
        self.vector_store = vector_store
        self.embedder = embedder

    async def search(
        self,
        kb_name: str,
        query: str,
        top_k: int = 20,
        filter: dict | None = None,
    ) -> list[tuple[Chunk, float]]:
        """向量语义检索。

        流程：
        1. 调用 embedder.embed_query(query) 生成查询向量
        2. 调用 vector_store.search(kb_name, query_vector, top_k, filter)
        3. 返回 (chunk, similarity_score) 列表

        Args:
            kb_name: 知识库名称。
            query: 查询文本。
            top_k: 返回结果数。
            filter: ChromaDB metadata 过滤条件。

        Returns:
            (chunk, similarity_score) 列表，按相似度降序。
        """
        # 生成查询向量
        query_vector = self.embedder.embed_query(query)

        # 向量检索（返回 chunk_id, score, metadata）
        raw_results = await self.vector_store.search(
            kb_name=kb_name,
            query_vector=query_vector,
            top_k=top_k,
            filter=filter,
        )

        # 通过 DocumentStore 重建 Chunk 对象
        # 为避免循环导入，这里直接从 vector_store 获取的 metadata 重建
        # 注意：vector_store 只返回 (chunk_id, score, metadata)，不返回 Chunk
        # 完整的 Chunk 重建需要 DocumentStore，由 RAGRetriever 协调完成
        logger.debug("向量检索: kb=%s, query=%s, results=%d", kb_name, query[:50], len(raw_results))

        # 返回原始结果，由 RAGRetriever 负责重建 Chunk
        return raw_results
