"""向量存储抽象基类。

定义所有向量存储后端必须实现的接口，
将存储层与业务数据模型（Chunk）解耦。
"""

import logging
from abc import ABC, abstractmethod

from graph_agent.rag.rag_types import Chunk

logger = logging.getLogger(__name__)


class BaseVectorStore(ABC):
    """向量存储抽象基类。

    设计原则：
    - add_chunks() 接收完整 Chunk 对象以获取 id/embedding/metadata，
      但存储层不持有对 Chunk 数据模型的业务逻辑引用。
    - search() 返回 (chunk_id, score, metadata) 元组，而非 Chunk 对象。
      这确保向量存储层与 Chunk 数据模型完全解耦。
    - 每个知识库对应一个独立的向量集合（collection）。
    """

    def __init__(self, persist_dir: str = "data/rag/"):
        """初始化向量存储。

        Args:
            persist_dir: 持久化目录路径。
        """
        self.persist_dir = persist_dir

    # ── 集合管理 ──────────────────────────────────────────────────────────

    @abstractmethod
    async def create_collection(self, kb_name: str, dimension: int) -> None:
        """创建知识库对应的向量集合。

        若集合已存在，此方法应静默跳过（幂等）。

        Args:
            kb_name: 知识库名称，用作集合名。
            dimension: 嵌入向量的维度。

        Raises:
            ValueError: 维度 <= 0。
            RuntimeError: 底层存储创建失败。
        """
        ...

    @abstractmethod
    async def delete_collection(self, kb_name: str) -> None:
        """删除知识库对应的向量集合及其所有数据。

        若集合不存在，此方法应静默跳过（幂等）。

        Args:
            kb_name: 知识库名称。
        """
        ...

    @abstractmethod
    async def collection_exists(self, kb_name: str) -> bool:
        """检查集合是否存在。

        Args:
            kb_name: 知识库名称。

        Returns:
            True 表示集合存在。
        """
        ...

    @abstractmethod
    async def collection_stats(self, kb_name: str) -> dict:
        """获取集合的统计信息。

        Args:
            kb_name: 知识库名称。

        Returns:
            统计字典，应包含：
            - name: 集合名称
            - count: 向量总数
            - dimension: 向量维度（若可知）
        """
        ...

    @abstractmethod
    async def get_dimension(self, kb_name: str) -> int | None:
        """获取集合的向量维度。

        Args:
            kb_name: 知识库名称。

        Returns:
            向量维度，若集合不存在或无法确定则返回 None。
        """
        ...

    # ── 数据操作 ──────────────────────────────────────────────────────────

    @abstractmethod
    async def add_chunks(self, kb_name: str, chunks: list[Chunk]) -> None:
        """批量添加分块的嵌入向量到向量存储。

        仅提取每个 Chunk 的 id、embedding 和 metadata 写入存储，
        不持有对 Chunk 对象的引用。

        若 chunk.embedding 为 None，则跳过该分块并记录警告。

        Args:
            kb_name: 知识库名称。
            chunks: 包含嵌入向量的 Chunk 对象列表。

        Raises:
            ValueError: 集合不存在或维度不匹配。
        """
        ...

    @abstractmethod
    async def search(
        self,
        kb_name: str,
        query_vector: list[float],
        top_k: int = 10,
        filter: dict | None = None,
    ) -> list[tuple[str, float, dict]]:
        """向量相似度检索。

        返回 (chunk_id, score, metadata) 元组列表，而非 Chunk 对象。
        score 为余弦相似度（值域 [0, 1]），按降序排列。

        Args:
            kb_name: 知识库名称。
            query_vector: 查询向量。
            top_k: 返回的最大结果数。
            filter: 元数据过滤条件（后端特定格式）。

        Returns:
            (chunk_id, similarity_score, metadata_dict) 列表，按分数降序。

        Raises:
            ValueError: 集合不存在或向量维度不匹配。
        """
        ...

    @abstractmethod
    async def delete_by_doc_id(self, kb_name: str, doc_id: str) -> int:
        """按文档 ID 删除向量。

        删除所有 metadata 中 doc_id 匹配的分块向量。

        Args:
            kb_name: 知识库名称。
            doc_id: 文档 ID。

        Returns:
            实际删除的向量数量。
        """
        ...

    # ── 工具方法 ──────────────────────────────────────────────────────────

    async def get_chunk_ids(self, kb_name: str) -> set[str]:
        """获取集合中所有 chunk_id（用于索引一致性校验）。

        默认实现返回空集合，子类可覆盖以实现高效的分页查询。

        Args:
            kb_name: 知识库名称。

        Returns:
            chunk_id 集合。
        """
        return set()
