"""向量存储注册表。

管理后端类型到实现类的映射，提供统一的向量存储创建/获取接口。
"""

import logging
from typing import Type

from graph_agent.rag.rag_types import VectorStoreType

from .base_vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


class VectorStoreRegistry:
    """向量存储注册表。

    将 VectorStoreType 枚举值映射到具体的 BaseVectorStore 子类，
    管理已创建的存储实例，按知识库名称索引。

    使用方式：
        registry = VectorStoreRegistry()
        registry.register_backend(VectorStoreType.CHROMADB, ChromaVectorStore)

        store = registry.get_or_create_store(
            kb_name="my_kb",
            backend=VectorStoreType.CHROMADB,
            dimension=1536,
            persist_dir="data/rag/",
        )
    """

    _backends: dict[VectorStoreType, Type[BaseVectorStore]] = {}
    _instances: dict[str, BaseVectorStore] = {}
    _client_cache: dict[str, object] = {}  # persist_dir -> PersistentClient

    @classmethod
    def register_backend(
        cls,
        backend_type: VectorStoreType,
        store_cls: Type[BaseVectorStore],
    ) -> None:
        """注册向量存储后端实现类。

        Args:
            backend_type: 后端类型枚举值。
            store_cls: BaseVectorStore 的子类。

        Raises:
            TypeError: store_cls 不是 BaseVectorStore 的子类。
        """
        if not issubclass(store_cls, BaseVectorStore):
            raise TypeError(
                f"store_cls 必须是 BaseVectorStore 的子类，"
                f"收到 {store_cls.__name__}"
            )
        cls._backends[backend_type] = store_cls
        logger.info("已注册向量存储后端: %s -> %s", backend_type.value, store_cls.__name__)

    @classmethod
    def get_backend(cls, backend_type: VectorStoreType) -> Type[BaseVectorStore] | None:
        """获取指定后端类型的实现类。

        Args:
            backend_type: 后端类型枚举值。

        Returns:
            实现类，未注册时返回 None。
        """
        return cls._backends.get(backend_type)

    @classmethod
    def create_store(
        cls,
        kb_name: str,
        backend: VectorStoreType = VectorStoreType.CHROMADB,
        dimension: int = 0,
        persist_dir: str = "data/rag/",
    ) -> BaseVectorStore:
        """创建向量存储实例。

        每次调用都创建新的实例对象，不检查缓存。

        Args:
            kb_name: 知识库名称。
            backend: 后端类型。
            dimension: 向量维度。
            persist_dir: 持久化目录。

        Returns:
            BaseVectorStore 实例。

        Raises:
            ValueError: 后端类型未注册。
        """
        store_cls = cls._backends.get(backend)
        if store_cls is None:
            available = [bt.value for bt in cls._backends]
            raise ValueError(
                f"未注册的向量存储后端: '{backend.value}'，可用: {available}"
            )

        store = store_cls(persist_dir=persist_dir)
        logger.info(
            "创建向量存储: kb_name=%s, backend=%s, dimension=%d",
            kb_name, backend.value, dimension,
        )
        return store

    @classmethod
    def get_or_create_store(
        cls,
        kb_name: str,
        backend: VectorStoreType = VectorStoreType.CHROMADB,
        dimension: int = 0,
        persist_dir: str = "data/rag/",
    ) -> BaseVectorStore:
        """获取或创建向量存储实例（按 kb_name 缓存）。

        若 kb_name 对应的实例已存在则直接返回，否则创建新实例。

        Args:
            kb_name: 知识库名称。
            backend: 后端类型。
            dimension: 向量维度。
            persist_dir: 持久化目录。

        Returns:
            BaseVectorStore 实例。
        """
        cache_key = f"{persist_dir}:{kb_name}"
        if cache_key in cls._instances:
            return cls._instances[cache_key]

        store = cls.create_store(kb_name, backend, dimension, persist_dir)
        cls._instances[cache_key] = store
        return store

    @classmethod
    def get_stored_instance(cls, kb_name: str, persist_dir: str = "data/rag/") -> BaseVectorStore | None:
        """获取已缓存的存储实例（不创建）。

        Args:
            kb_name: 知识库名称。
            persist_dir: 持久化目录。

        Returns:
            缓存的实例，不存在时返回 None。
        """
        cache_key = f"{persist_dir}:{kb_name}"
        return cls._instances.get(cache_key)

    @classmethod
    def invalidate_store(cls, kb_name: str, persist_dir: str = "data/rag/") -> None:
        """使指定知识库的缓存实例失效。

        Args:
            kb_name: 知识库名称。
            persist_dir: 持久化目录。
        """
        cache_key = f"{persist_dir}:{kb_name}"
        if cache_key in cls._instances:
            del cls._instances[cache_key]
            logger.debug("已失效向量存储缓存: %s", cache_key)

    @classmethod
    def list_backends(cls) -> list[str]:
        """列出所有已注册的后端类型名称。"""
        return [bt.value for bt in cls._backends]

    @classmethod
    def clear_all(cls) -> None:
        """清空所有缓存和注册信息。"""
        cls._backends.clear()
        cls._instances.clear()
        cls._client_cache.clear()
        logger.debug("已清空向量存储注册表")


# ── 自动注册内置后端 ──────────────────────────────────────────────────────

# 延迟导入以避免循环依赖，在模块首次加载时注册 ChromaDB 后端
def _register_builtin_backends() -> None:
    """注册内置向量存储后端。"""
    try:
        from .chroma_store import ChromaVectorStore
        VectorStoreRegistry.register_backend(VectorStoreType.CHROMADB, ChromaVectorStore)
    except ImportError as e:
        logger.warning("无法注册 ChromaDB 后端: %s", e)


_register_builtin_backends()
