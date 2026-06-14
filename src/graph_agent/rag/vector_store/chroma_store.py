"""ChromaDB 向量存储实现。

基于 chromadb.PersistentClient，每个知识库映射为一个 ChromaDB collection。
使用 HNSW 索引（ChromaDB 默认），支持元数据过滤和余弦相似度检索。
"""

import logging
import math
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from graph_agent.rag.rag_types import Chunk

from .base_vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


class ChromaVectorStore(BaseVectorStore):
    """ChromaDB 向量存储实现。

    特点：
    - 使用 PersistentClient 进行本地持久化。
    - 每个知识库对应一个独立的 ChromaDB collection。
    - 使用 HNSW 索引和余弦距离空间。
    - L2 距离自动转换为余弦相似度近似值。
    - 支持 ChromaDB 的 where 子句进行元数据过滤。

    持久化目录结构：
        {persist_dir}/chroma_db/   <- ChromaDB 数据目录
    """

    # ChromaDB 默认使用 L2 空间，我们指定使用 cosine 以获得更直接的相似度
    _DEFAULT_SPACE = "cosine"
    _DEFAULT_METADATA = {"hnsw:space": _DEFAULT_SPACE}

    def __init__(self, persist_dir: str = "data/rag/"):
        """初始化 ChromaDB 向量存储。

        Args:
            persist_dir: 持久化根目录，ChromaDB 数据存储在 {persist_dir}/chroma_db/。
        """
        super().__init__(persist_dir)
        self._chroma_dir = Path(persist_dir) / "chroma_db"
        self._chroma_dir.mkdir(parents=True, exist_ok=True)

        self._client: Optional[chromadb.PersistentClient] = None
        self._collection_cache: dict[str, chromadb.Collection] = {}

    # ── 客户端管理 ────────────────────────────────────────────────────────

    @property
    def client(self) -> chromadb.PersistentClient:
        """懒加载获取 ChromaDB PersistentClient（线程安全由调用方保证）。"""
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=str(self._chroma_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            logger.debug("ChromaDB 客户端已初始化: %s", self._chroma_dir)
        return self._client

    def _get_collection(self, kb_name: str) -> chromadb.Collection:
        """获取或缓存 ChromaDB collection 对象。

        Args:
            kb_name: 知识库名称。

        Returns:
            ChromaDB Collection 对象。

        Raises:
            ValueError: 集合不存在。
        """
        if kb_name in self._collection_cache:
            return self._collection_cache[kb_name]

        try:
            collection = self.client.get_collection(
                name=kb_name,
            )
            self._collection_cache[kb_name] = collection
            return collection
        except Exception:
            raise ValueError(f"向量集合不存在: '{kb_name}'")

    def _get_collection_optional(self, kb_name: str) -> Optional[chromadb.Collection]:
        """安全获取 collection，不存在时返回 None。"""
        try:
            return self._get_collection(kb_name)
        except ValueError:
            return None

    # ── 集合管理 ──────────────────────────────────────────────────────────

    async def create_collection(self, kb_name: str, dimension: int) -> None:
        """创建知识库对应的 ChromaDB 集合。

        若集合已存在则静默跳过（幂等操作）。

        Args:
            kb_name: 知识库名称（用作 ChromaDB collection 名）。
            dimension: 嵌入向量维度。

        Raises:
            ValueError: dimension <= 0。
        """
        if dimension <= 0:
            raise ValueError(f"向量维度必须大于 0，收到 {dimension}")

        try:
            existing = self.client.get_collection(name=kb_name)
            logger.debug("集合 '%s' 已存在，跳过创建 (count=%d)", kb_name, existing.count())
            self._collection_cache[kb_name] = existing
            return
        except Exception:
            pass  # 集合不存在，继续创建

        collection = self.client.create_collection(
            name=kb_name,
            metadata={
                **self._DEFAULT_METADATA,
                "dimension": str(dimension),
            },
        )
        self._collection_cache[kb_name] = collection
        logger.info("已创建 ChromaDB 集合: '%s' (dimension=%d)", kb_name, dimension)

    async def delete_collection(self, kb_name: str) -> None:
        """删除知识库对应的 ChromaDB 集合。

        若集合不存在则静默跳过。

        Args:
            kb_name: 知识库名称。
        """
        try:
            self.client.delete_collection(name=kb_name)
            logger.info("已删除 ChromaDB 集合: '%s'", kb_name)
        except Exception:
            logger.debug("集合 '%s' 不存在，跳过删除", kb_name)

        self._collection_cache.pop(kb_name, None)

    async def collection_exists(self, kb_name: str) -> bool:
        """检查 ChromaDB 集合是否存在。

        Args:
            kb_name: 知识库名称。

        Returns:
            True 表示集合存在。
        """
        try:
            self.client.get_collection(name=kb_name)
            return True
        except Exception:
            return False

    async def collection_stats(self, kb_name: str) -> dict:
        """获取集合的统计信息。

        Args:
            kb_name: 知识库名称。

        Returns:
            统计字典，包含 name、count 和 dimension。
            集合不存在时返回 count=0。
        """
        try:
            collection = self._get_collection(kb_name)
            count = collection.count()
            metadata = collection.metadata or {}
            dimension = int(metadata.get("dimension", 0))
            return {
                "name": kb_name,
                "count": count,
                "dimension": dimension,
            }
        except ValueError:
            return {"name": kb_name, "count": 0, "dimension": 0}

    async def get_dimension(self, kb_name: str) -> int | None:
        """获取集合的向量维度。

        Args:
            kb_name: 知识库名称。

        Returns:
            向量维度，集合不存在时返回 None。
        """
        try:
            collection = self._get_collection(kb_name)
            metadata = collection.metadata or {}
            dim_str = metadata.get("dimension", "")
            if dim_str:
                return int(dim_str)
            # 回退：尝试从已有数据中推断维度
            if collection.count() > 0:
                sample = collection.get(limit=1, include=["embeddings"])
                if sample["embeddings"] and sample["embeddings"][0]:
                    return len(sample["embeddings"][0])
            return None
        except ValueError:
            return None

    # ── 数据操作 ──────────────────────────────────────────────────────────

    async def add_chunks(self, kb_name: str, chunks: list[Chunk]) -> None:
        """批量添加分块的嵌入向量到 ChromaDB。

        仅提取 id、embedding、metadata 字段；跳过 embedding 为 None 的分块。

        Args:
            kb_name: 知识库名称。
            chunks: 包含嵌入向量的 Chunk 对象列表。

        Raises:
            ValueError: 集合不存在。
        """
        if not chunks:
            return

        collection = self._get_collection(kb_name)

        ids: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict] = []
        documents: list[str] = []

        for chunk in chunks:
            if chunk.embedding is None:
                logger.warning("跳过无嵌入向量的分块: %s", chunk.id)
                continue

            ids.append(chunk.id)
            embeddings.append(chunk.embedding)
            # 构造 metadata：包含 doc_id 和来源信息用于过滤和删除
            chunk_meta = {
                "doc_id": chunk.doc_id,
                "kb_name": chunk.kb_name,
                "chunk_index": chunk.chunk_index,
                "token_count": chunk.token_count,
            }
            # 合并 Chunk.metadata 中的可序列化字段
            if chunk.metadata:
                for key, value in chunk.metadata.items():
                    # ChromaDB metadata 仅支持 str, int, float, bool
                    if isinstance(value, (str, int, float, bool)):
                        chunk_meta[key] = value
                    elif isinstance(value, list):
                        chunk_meta[key] = ",".join(str(v) for v in value)
            metadatas.append(chunk_meta)
            documents.append(chunk.content)

        if not ids:
            logger.warning("所有分块均无嵌入向量，跳过添加 (kb_name=%s)", kb_name)
            return

        collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents,
        )
        logger.info(
            "已添加 %d 个向量到集合 '%s' (总 chunk 数 %d)",
            len(ids), kb_name, len(chunks),
        )

    async def search(
        self,
        kb_name: str,
        query_vector: list[float],
        top_k: int = 10,
        filter: dict | None = None,
    ) -> list[tuple[str, float, dict]]:
        """向量相似度检索。

        使用 ChromaDB 的余弦空间，距离值即为 1 - cos_sim，
        转换为余弦相似度分数返回。

        Args:
            kb_name: 知识库名称。
            query_vector: 查询向量。
            top_k: 返回的最大结果数。
            filter: ChromaDB where 格式的元数据过滤条件。

        Returns:
            (chunk_id, cosine_similarity, metadata_dict) 列表，按相似度降序。

        Raises:
            ValueError: 集合不存在或向量维度不匹配。
        """
        collection = self._get_collection(kb_name)

        # ChromaDB query 参数
        query_kwargs = {
            "query_embeddings": [query_vector],
            "n_results": top_k,
            "include": ["metadatas", "distances", "documents"],
        }
        if filter:
            query_kwargs["where"] = filter

        try:
            results = collection.query(**query_kwargs)
        except Exception as e:
            logger.error("ChromaDB 查询失败 (kb_name=%s): %s", kb_name, e)
            raise

        # 解析结果
        output: list[tuple[str, float, dict]] = []

        if not results["ids"] or not results["ids"][0]:
            return output

        result_ids = results["ids"][0]
        result_distances = results.get("distances", [[0.0] * len(result_ids)])[0]
        result_metadatas = results.get("metadatas", [[{}] * len(result_ids)])[0]

        for chunk_id, distance, metadata in zip(result_ids, result_distances, result_metadatas):
            # 余弦空间下：distance 是 cosine distance (1 - cos_sim)
            # 转换为余弦相似度: cos_sim = 1 - distance
            cosine_similarity = self._distance_to_similarity(distance)
            output.append((chunk_id, cosine_similarity, metadata or {}))

        # 按相似度降序排列（ChromaDB 默认已是降序）
        output.sort(key=lambda x: x[1], reverse=True)

        return output

    async def delete_by_doc_id(self, kb_name: str, doc_id: str) -> int:
        """按文档 ID 删除向量。

        通过 ChromaDB 的 where 过滤删除所有 metadata.doc_id 匹配的向量。

        Args:
            kb_name: 知识库名称。
            doc_id: 文档 ID。

        Returns:
            实际删除的向量数量。集合不存在时返回 0。
        """
        collection = self._get_collection_optional(kb_name)
        if collection is None:
            logger.debug("集合 '%s' 不存在，跳过删除 (doc_id=%s)", kb_name, doc_id)
            return 0

        # 先查询匹配的 id 以获得计数
        try:
            existing = collection.get(
                where={"doc_id": doc_id},
                include=[],
            )
        except Exception:
            logger.debug("查询 doc_id=%s 失败，集合可能为空", doc_id)
            return 0

        if not existing["ids"]:
            return 0

        count = len(existing["ids"])
        collection.delete(ids=existing["ids"])
        logger.info("已从集合 '%s' 删除 %d 个向量 (doc_id=%s)", kb_name, count, doc_id)
        return count

    # ── 辅助方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _distance_to_similarity(distance: float) -> float:
        """将 ChromaDB 距离值转换为余弦相似度。

        ChromaDB 使用余弦空间时，distance = 1 - cos_sim，
        因此 cos_sim = 1 - distance。

        对于浮点精度导致的微小负值，截断为 0。

        Args:
            distance: ChromaDB 返回的距离值。

        Returns:
            [0, 1] 范围内的余弦相似度。
        """
        similarity = 1.0 - distance
        # 修正浮点精度导致的微小负值
        if similarity < 0:
            similarity = 0.0
        elif similarity > 1.0:
            similarity = 1.0
        return similarity

    async def get_chunk_ids(self, kb_name: str) -> set[str]:
        """获取集合中所有 chunk_id（用于一致性校验）。

        Args:
            kb_name: 知识库名称。

        Returns:
            chunk_id 集合。
        """
        collection = self._get_collection_optional(kb_name)
        if collection is None or collection.count() == 0:
            return set()

        all_ids: set[str] = set()
        # 分页获取所有 ID
        offset = 0
        page_size = 1000
        while True:
            batch = collection.get(limit=page_size, offset=offset, include=[])
            if not batch["ids"]:
                break
            all_ids.update(batch["ids"])
            offset += page_size
            if len(batch["ids"]) < page_size:
                break

        return all_ids
