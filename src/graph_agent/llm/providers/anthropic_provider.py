"""Anthropic Claude 系列模型提供商。"""
from langchain_core.language_models import BaseChatModel

from graph_agent.llm.base import LLMProvider, LLMConfig, LLMFactory

try:
    from langchain_anthropic import ChatAnthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class AnthropicProvider(LLMProvider):
    """Anthropic Claude 系列模型提供商。"""

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def validate_config(self) -> bool:
        """验证配置是否完整有效。"""
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "langchain-anthropic 包未安装。请运行: pip install langchain-anthropic"
            )
        return bool(self.config.model and self.config.api_key)

    def get_chat_model(self) -> BaseChatModel:
        """获取 LangChain 兼容的 ChatAnthropic 模型实例。"""
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "langchain-anthropic 包未安装。请运行: pip install langchain-anthropic"
            )

        from graph_agent.llm.base import LLMFactory
        callbacks = LLMFactory.get_callbacks()
        return ChatAnthropic(
            model=self.config.model,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout,
            callbacks=callbacks if callbacks else None,
        )


# 注册提供商
LLMFactory.register_provider("anthropic", AnthropicProvider)
