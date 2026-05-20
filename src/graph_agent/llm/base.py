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


class _HookEnabledChatModel:
    """包装 BaseChatModel，在 invoke 调用前后触发 Hook 检查点。

    对其他所有属性和方法的访问透明代理到内部模型，
    确保 bind_tools 等方法正常工作。
    """

    def __init__(self, inner: BaseChatModel, provider_name: str, model_name: str):
        self._inner = inner
        self._provider_name = provider_name
        self._model_name = model_name

    def invoke(self, messages, config=None, **kwargs):
        from graph_agent.hook import get_hook_executor, HookContext, HookAction, HookAbortError
        from langchain_core.messages import AIMessage

        executor = get_hook_executor()
        caller = ""
        if config and isinstance(config, dict):
            caller = config.get("run_name", "")

        # — before_llm_call —
        ctx = HookContext(
            checkpoint="before_llm_call",
            llm_messages=list(messages) if messages else None,
            llm_model=self._model_name,
            llm_caller=caller or self._provider_name,
        )
        ctx, decision = executor.execute("before_llm_call", ctx)

        if decision and decision.action == HookAction.ABORT:
            raise HookAbortError(decision.reason)
        if decision and decision.action == HookAction.SKIP:
            return AIMessage(content=decision.fallback_result or "")

        # Type 1 may have modified messages
        if ctx.llm_messages is not None:
            messages = ctx.llm_messages

        # — invoke —
        response = self._inner.invoke(messages, config=config, **kwargs)

        # — after_llm_call —
        content = ""
        if hasattr(response, "content"):
            content = response.content or ""
        else:
            content = str(response)

        token_usage = {}
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            um = response.usage_metadata
            token_usage = {
                "input_tokens": um.get("input_tokens", 0),
                "output_tokens": um.get("output_tokens", 0),
                "total_tokens": um.get("total_tokens", 0),
            }
        elif hasattr(response, "response_metadata") and response.response_metadata:
            rm = response.response_metadata
            tu = rm.get("token_usage", {})
            if tu:
                token_usage = dict(tu)

        ctx = HookContext(
            checkpoint="after_llm_call",
            llm_model=self._model_name,
            llm_caller=caller or self._provider_name,
            llm_response=content,
            llm_token_usage=token_usage if token_usage else None,
        )
        executor.execute("after_llm_call", ctx)

        return response

    def __getattr__(self, name: str):
        # 代理所有其他属性/方法到内部模型
        return getattr(self._inner, name)


def _wrap_provider_with_hooks(provider: LLMProvider) -> None:
    """在 LLMProvider 的 chat model 上包装 Hook 检查点。

    替换 provider 的 get_chat_model 方法，使其返回 _HookEnabledChatModel。
    包装类透明代理 bind_tools 等方法到内部模型。
    """
    original_get_chat_model = provider.get_chat_model

    def hooked_get_chat_model() -> BaseChatModel:
        model = original_get_chat_model()
        wrapped = _HookEnabledChatModel(model, provider.provider_name, provider.model_name)
        return wrapped  # type: ignore[return-value]

    provider.get_chat_model = hooked_get_chat_model  # type: ignore[method-assign]


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
        """注册 LangChain callback handler，所有 LLM 调用自动拦截。

        已废弃：LLM/工具调用追踪已迁移到 Hook 机制（hook/builtin/tracer_hooks.py）。
        保留此方法以兼容调用方，但 callback 不再被注入到 provider。
        """
        if callback not in cls._callbacks:
            cls._callbacks.append(callback)

    @classmethod
    def get_callbacks(cls) -> list:
        """获取当前注册的所有 callback（已废弃，返回空列表）。"""
        return list(cls._callbacks)

    @classmethod
    def create(cls, config: LLMConfig, wrap_hooks: bool = True) -> LLMProvider:
        """根据配置创建 LLM 提供商实例。

        Args:
            config: LLM 配置
            wrap_hooks: 是否在 chat model 上包装 Hook 检查点，默认 True
        """
        provider_class = cls._providers.get(config.provider)
        if not provider_class:
            raise ValueError(f"不支持的 LLM 提供商: {config.provider}, "
                           f"可用提供商: {list(cls._providers.keys())}")
        provider = provider_class(config)
        if not provider.validate_config():
            raise ValueError(f"LLM 配置验证失败，请检查环境变量配置")
        if wrap_hooks:
            _wrap_provider_with_hooks(provider)
        return provider

    @classmethod
    def create_from_env(cls, prefix: str = "LLM_",
                        wrap_hooks: bool = True) -> LLMProvider:
        """从环境变量加载配置并创建 LLM 实例。"""
        config = LLMConfig.from_env(prefix)
        return cls.create(config, wrap_hooks=wrap_hooks)

    @classmethod
    def list_providers(cls) -> list[str]:
        """列出所有已注册的提供商名称。"""
        return list(cls._providers.keys())
