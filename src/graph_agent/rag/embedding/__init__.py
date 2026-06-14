"""嵌入器注册中心。

自动注册内置嵌入器，支持按提供者名称发现和获取嵌入器类。
"""

import logging
from typing import Type

from graph_agent.rag.rag_types import EmbedderType

from .base_embedder import BaseEmbedder
from .openai_embedder import OpenAIEmbedder
from .local_embedder import LocalEmbedder

logger = logging.getLogger(__name__)


class EmbedderRegistry:
    """嵌入器注册中心。

    将提供者名称映射到嵌入器类，自动发现并注册内置嵌入器。
    外部模块可通过 register() 注册自定义嵌入器。
    """

    _embedders: dict[str, Type[BaseEmbedder]] = {}

    @classmethod
    def register(cls, name: str, embedder_cls: Type[BaseEmbedder]) -> None:
        """注册嵌入器类。

        Args:
            name: 提供者名称（如 "openai", "local"）
            embedder_cls: BaseEmbedder 的子类
        """
        cls._embedders[name] = embedder_cls
        logger.debug("已注册嵌入器: %s -> %s", name, embedder_cls.__name__)

    @classmethod
    def get(cls, name: str) -> Type[BaseEmbedder] | None:
        """根据提供者名称获取嵌入器类。

        Args:
            name: 提供者名称

        Returns:
            嵌入器类，不存在时返回 None
        """
        return cls._embedders.get(name)

    @classmethod
    def list_embedders(cls) -> list[str]:
        """列出所有已注册的嵌入器提供者名称。"""
        return list(cls._embedders.keys())

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseEmbedder:
        """创建嵌入器实例。

        Args:
            name: 提供者名称
            **kwargs: 传递给嵌入器构造函数的参数

        Returns:
            嵌入器实例

        Raises:
            ValueError: 未知的提供者名称
        """
        embedder_cls = cls.get(name)
        if embedder_cls is None:
            available = ", ".join(cls.list_embedders())
            raise ValueError(
                f"未知的嵌入提供者: '{name}'，可用提供者: {available}"
            )
        return embedder_cls(**kwargs)


# 自动注册内置嵌入器
EmbedderRegistry.register(EmbedderType.OPENAI.value, OpenAIEmbedder)
EmbedderRegistry.register(EmbedderType.LOCAL.value, LocalEmbedder)
