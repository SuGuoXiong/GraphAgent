"""GraphAgent LLM 模块。

提供统一的多 LLM 提供商支持，封装不同 LLM 的实现差异。

使用示例:
    from graph_agent.llm import LLMFactory

    # 从环境变量创建 LLM 实例
    llm_provider = LLMFactory.create_from_env()

    # 获取 LangChain 兼容的聊天模型
    chat_model = llm_provider.get_chat_model()

    # 直接使用
    response = chat_model.invoke("你好")
"""

# 先导入 providers，触发自动注册
from graph_agent.llm.providers import auto_discover_providers
auto_discover_providers()

from graph_agent.llm.base import LLMConfig, LLMProvider, LLMFactory

__all__ = ["LLMConfig", "LLMProvider", "LLMFactory"]
