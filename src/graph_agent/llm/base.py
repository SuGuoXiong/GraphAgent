"""LLM 模块抽象基类和工厂。

提供统一的 LLM 接口，支持多种提供商。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Optional, Type

from langchain_core.language_models import BaseChatModel


@dataclass
class LLMConfig:
    """LLM 配置数据类。"""

    provider: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    timeout: Optional[int] = 60

    @classmethod
    def from_env(cls, prefix: str = "LLM_") -> "LLMConfig":
        """从环境变量加载配置。"""
        import os
        from dotenv import load_dotenv

        load_dotenv()

        def get_env(name: str, default=None):
            return os.getenv(f"{prefix}{name}", default)

        return cls(
            provider=get_env("PROVIDER", "openai"),
            model=get_env("MODEL", "gpt-3.5-turbo"),
            api_key=get_env("API_KEY"),
            base_url=get_env("BASE_URL"),
            temperature=float(get_env("TEMPERATURE", "0.0")),
            max_tokens=int(get_env("MAX_TOKENS")) if get_env("MAX_TOKENS") else None,
            timeout=int(get_env("TIMEOUT", "60")) if get_env("TIMEOUT") else None,
        )


class LLMProvider(ABC):
    """LLM 提供商抽象基类。"""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    def get_chat_model(self) -> BaseChatModel:
        """获取 LangChain 兼容的聊天模型实例。"""
        pass

    @abstractmethod
    def validate_config(self) -> bool:
        """验证配置是否完整有效。"""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """提供商名称。"""
        pass

    @property
    def model_name(self) -> str:
        """模型名称。"""
        return self.config.model


class LLMFactory:
    """LLM 实例工厂类。"""

    _providers: ClassVar[dict[str, Type[LLMProvider]]] = {}
    _callbacks: ClassVar[list] = []

    @classmethod
    def register_provider(cls, name: str, provider_class: Type[LLMProvider]):
        """注册新的 LLM 提供商。"""
        cls._providers[name] = provider_class

    @classmethod
    def register_callback(cls, callback) -> None:
        """注册 LangChain callback handler，所有 LLM 调用自动拦截。"""
        if callback not in cls._callbacks:
            cls._callbacks.append(callback)

    @classmethod
    def get_callbacks(cls) -> list:
        """获取当前注册的所有 callback。"""
        return list(cls._callbacks)

    @classmethod
    def create(cls, config: LLMConfig) -> LLMProvider:
        """根据配置创建 LLM 提供商实例。"""
        provider_class = cls._providers.get(config.provider)
        if not provider_class:
            raise ValueError(f"不支持的 LLM 提供商: {config.provider}, "
                           f"可用提供商: {list(cls._providers.keys())}")
        provider = provider_class(config)
        if not provider.validate_config():
            raise ValueError(f"LLM 配置验证失败，请检查环境变量配置")
        return provider

    @classmethod
    def create_from_env(cls, prefix: str = "LLM_") -> LLMProvider:
        """从环境变量加载配置并创建 LLM 实例。"""
        config = LLMConfig.from_env(prefix)
        return cls.create(config)

    @classmethod
    def list_providers(cls) -> list[str]:
        """列出所有已注册的提供商名称。"""
        return list(cls._providers.keys())
