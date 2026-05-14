"""OpenAI / OpenAI 兼容 API 提供商。

支持 OpenAI、DeepSeek、通义千问等所有兼容 OpenAI API 的服务。
"""
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

from graph_agent.llm.base import LLMProvider, LLMConfig, LLMFactory


class OpenAIProvider(LLMProvider):
    """OpenAI / OpenAI 兼容 API 提供商。"""

    @property
    def provider_name(self) -> str:
        return "openai"

    def validate_config(self) -> bool:
        """验证配置是否完整有效。"""
        if not self.config.model:
            return False

        # 如果配置了 base_url，可能不需要 api_key（如本地部署的兼容服务）
        if self.config.base_url and not self.config.api_key:
            return True

        # 默认情况下需要 api_key
        return self.config.api_key is not None

    def get_chat_model(self) -> BaseChatModel:
        """获取 LangChain 兼容的 ChatOpenAI 模型实例。"""
        return ChatOpenAI(
            model=self.config.model,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout,
        )


# 注册提供商
LLMFactory.register_provider("openai", OpenAIProvider)
