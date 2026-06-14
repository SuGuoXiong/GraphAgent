"""分块策略注册中心。

自动注册内置分块器，支持按策略名称发现和获取分块器类。
"""

import logging
from typing import Type

from graph_agent.rag.rag_types import ChunkStrategy

from .base_chunker import BaseChunker
from .token_chunker import TokenChunker
from .recursive_chunker import RecursiveChunker
from .semantic_chunker import SemanticChunker
from .markdown_aware_chunker import MarkdownAwareChunker

logger = logging.getLogger(__name__)


class ChunkerRegistry:
    """分块策略注册中心。

    将策略名称映射到分块器类，自动发现并注册内置分块器。
    外部模块可通过 register() 注册自定义分块器。
    """

    _chunkers: dict[str, Type[BaseChunker]] = {}

    @classmethod
    def register(cls, name: str, chunker_cls: Type[BaseChunker]) -> None:
        """注册分块器类。

        Args:
            name: 策略名称（如 "token", "recursive", "semantic"）
            chunker_cls: BaseChunker 的子类
        """
        cls._chunkers[name] = chunker_cls
        logger.debug("已注册分块器: %s -> %s", name, chunker_cls.__name__)

    @classmethod
    def get(cls, name: str) -> Type[BaseChunker] | None:
        """根据策略名称获取分块器类。

        Args:
            name: 策略名称

        Returns:
            分块器类，不存在时返回 None
        """
        return cls._chunkers.get(name)

    @classmethod
    def list_chunkers(cls) -> list[str]:
        """列出所有已注册的分块器策略名称。"""
        return list(cls._chunkers.keys())

    @classmethod
    def create(cls, name: str, chunk_size: int = 512,
               chunk_overlap: int = 50, **kwargs) -> BaseChunker:
        """创建分块器实例。

        Args:
            name: 策略名称
            chunk_size: 分块大小（token 数）
            chunk_overlap: 重叠 token 数
            **kwargs: 传递给分块器构造函数的额外参数

        Returns:
            分块器实例

        Raises:
            ValueError: 未知的策略名称
        """
        chunker_cls = cls.get(name)
        if chunker_cls is None:
            available = ", ".join(cls.list_chunkers())
            raise ValueError(
                f"未知的分块策略: '{name}'，可用策略: {available}"
            )
        return chunker_cls(chunk_size=chunk_size, chunk_overlap=chunk_overlap, **kwargs)


# 自动注册内置分块器
ChunkerRegistry.register(ChunkStrategy.TOKEN.value, TokenChunker)
ChunkerRegistry.register(ChunkStrategy.RECURSIVE.value, RecursiveChunker)
ChunkerRegistry.register(ChunkStrategy.SEMANTIC.value, SemanticChunker)
ChunkerRegistry.register("markdown", MarkdownAwareChunker)
