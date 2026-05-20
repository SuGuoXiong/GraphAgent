"""Ollama 本地模型提供商。

支持本地部署的 Ollama 模型。
"""
from langchain_core.language_models import BaseChatModel

from graph_agent.llm.base import LLMProvider, LLMConfig, LLMFactory

try:
    from langchain_ollama import ChatOllama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False


class OllamaProvider(LLMProvider):
    """Ollama 本地模型提供商。"""

    @property
    def provider_name(self) -> str:
        return "ollama"

    def validate_config(self) -> bool:
        """验证配置是否完整有效。"""
        if not OLLAMA_AVAILABLE:
            raise ImportError(
                "langchain-ollama 包未安装。请运行: pip install langchain-ollama"
            )
        return bool(self.config.model)

    def get_chat_model(self) -> BaseChatModel:
        """获取 LangChain 兼容的 ChatOllama 模型实例。"""
        if not OLLAMA_AVAILABLE:
            raise ImportError(
                "langchain-ollama 包未安装。请运行: pip install langchain-ollama"
            )

        base_url = self.config.base_url or "http://localhost:11434"
        return ChatOllama(
            model=self.config.model,
            base_url=base_url,
            temperature=self.config.temperature,
            num_predict=self.config.max_tokens,
            timeout=self.config.timeout,
        )


# 注册提供商
LLMFactory.register_provider("ollama", OllamaProvider)
