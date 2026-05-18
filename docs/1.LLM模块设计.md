# GraphAgent LLM 模块设计文档

## 1. 概述

为了支持多种 LLM 提供商（OpenAI、Ollama、Anthropic、DeepSeek 等），GraphAgent 需要一个统一的 LLM 模块，用于封装不同 LLM 的实现差异，提供一致的调用接口和配置管理。

## 2. 设计目标

| 目标 | 说明 |
|------|------|
| **统一接口** | 所有 LLM 提供商使用相同的调用接口 |
| **配置驱动** | 通过环境变量配置切换 LLM 提供商，无需修改代码 |
| **易于扩展** | 添加新的 LLM 提供商只需新增实现类 |
| **LangChain 兼容** | 生成的 LLM 实例直接兼容 LangChain/LangGraph 生态 |
| **类型安全** | 使用类型注解确保配置和调用的正确性 |

## 3. 目录结构

```
src/graph_agent/
├── llm/
│   ├── __init__.py          # 公共接口导出
│   ├── base.py              # 抽象基类和工厂
│   ├── providers/           # 各提供商实现
│   │   ├── __init__.py
│   │   ├── openai.py        # OpenAI / 兼容 API（DeepSeek等）
│   │   ├── ollama.py        # Ollama 本地模型
│   │   ├── anthropic.py     # Anthropic Claude
│   │   └── ...              # 更多提供商
│   └── config.py            # 配置管理和环境变量解析
└── graph.py                 # Agent 图定义（从 llm 模块导入）
```

## 4. 核心组件设计

### 4.1 LLMProvider 抽象基类

定义所有 LLM 提供商必须实现的统一接口。

```python
from abc import ABC, abstractmethod
from langchain_core.language_models import BaseChatModel

class LLMProvider(ABC):
    """LLM 提供商抽象基类。"""

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
    @abstractmethod
    def model_name(self) -> str:
        """模型名称。"""
        pass
```

### 4.2 LLMFactory 工厂类

根据配置创建对应的 LLM 提供商实例。

```python
class LLMFactory:
    """LLM 实例工厂类。"""

    _providers: ClassVar[dict[str, Type[LLMProvider]]] = {}

    @classmethod
    def register_provider(cls, name: str, provider_class: Type[LLMProvider]):
        """注册新的 LLM 提供商。"""
        cls._providers[name] = provider_class

    @classmethod
    def create(cls, config: LLMConfig) -> LLMProvider:
        """根据配置创建 LLM 提供商实例。"""
        provider_class = cls._providers.get(config.provider)
        if not provider_class:
            raise ValueError(f"不支持的 LLM 提供商: {config.provider}")
        return provider_class(config)

    @classmethod
    def create_from_env(cls) -> LLMProvider:
        """从环境变量加载配置并创建 LLM 实例。"""
        config = LLMConfig.from_env()
        return cls.create(config)
```

### 4.3 LLMConfig 配置类

管理 LLM 配置，支持从环境变量加载。

```python
@dataclass
class LLMConfig:
    """LLM 配置数据类。"""

    provider: str                    # 提供商名称: openai, ollama, anthropic
    model: str                       # 模型名称
    api_key: Optional[str] = None    # API 密钥
    base_url: Optional[str] = None   # API 端点
    temperature: float = 0.0         # 温度参数
    max_tokens: Optional[int] = None # 最大生成长度
    timeout: Optional[int] = 60      # 超时时间（秒）

    @classmethod
    def from_env(cls, prefix: str = "LLM_") -> "LLMConfig":
        """从环境变量加载配置。"""
```

## 5. 环境变量配置规范

使用统一的环境变量前缀 `LLM_`，避免与其他配置冲突。

```ini
# 必需配置
LLM_PROVIDER=openai              # openai, ollama, anthropic
LLM_MODEL=gpt-4                  # 模型名称

# API 配置
LLM_API_KEY=sk-xxx               # API 密钥
LLM_BASE_URL=https://api...      # API 端点（可选，用于兼容 API）

# 模型参数
LLM_TEMPERATURE=0.0              # 温度
LLM_MAX_TOKENS=4096              # 最大生成长度
LLM_TIMEOUT=60                   # 超时时间
```

### 不同提供商的配置示例

**OpenAI / 兼容 API（DeepSeek）:**
```ini
LLM_PROVIDER=openai
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1
```

**Ollama（本地模型）:**
```ini
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b
LLM_BASE_URL=http://localhost:11434
```

**Anthropic:**
```ini
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet
LLM_API_KEY=sk-ant-xxx
```

## 6. 提供商实现规范

### 6.1 OpenAI 兼容提供商

支持 OpenAI、DeepSeek、通义千问等所有兼容 OpenAI API 的提供商。

```python
class OpenAIProvider(LLMProvider):
    """OpenAI / OpenAI 兼容 API 提供商。"""

    def __init__(self, config: LLMConfig):
        self.config = config

    def get_chat_model(self) -> ChatOpenAI:
        return ChatOpenAI(
            model=self.config.model,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout,
        )
```

### 6.2 Ollama 提供商

支持本地部署的 Ollama 模型。

```python
class OllamaProvider(LLMProvider):
    """Ollama 本地模型提供商。"""

    def get_chat_model(self) -> ChatOllama:
        return ChatOllama(
            model=self.config.model,
            base_url=self.config.base_url or "http://localhost:11434",
            temperature=self.config.temperature,
            num_predict=self.config.max_tokens,
        )
```

## 7. 扩展机制

### 7.1 添加新的 LLM 提供商

1. 在 `src/graph_agent/llm/providers/` 下创建新文件
2. 继承 `LLMProvider` 基类并实现所有抽象方法
3. 在模块中调用 `LLMFactory.register_provider()` 注册

示例：
```python
# providers/new_provider.py
from graph_agent.llm.base import LLMProvider, LLMFactory

class NewProvider(LLMProvider):
    # 实现抽象方法...
    pass

LLMFactory.register_provider("new_provider", NewProvider)
```

### 7.2 自动发现

LLM 模块支持自动扫描 `providers` 目录，自动注册所有提供商，无需手动导入。

## 8. 使用示例

### 8.1 基础使用

```python
from graph_agent.llm import LLMFactory

# 从环境变量创建 LLM 实例
llm_provider = LLMFactory.create_from_env()

# 获取 LangChain 兼容的聊天模型
chat_model = llm_provider.get_chat_model()

# 直接使用
response = chat_model.invoke("你好")
```

### 8.2 在 Graph.py 中使用

```python
from graph_agent.llm import LLMFactory
from graph_agent.tools import ToolCenter

# 初始化 LLM 和工具
llm = LLMFactory.create_from_env().get_chat_model()
tool_center = ToolCenter()
tool_center.auto_discover()

# 绑定工具
llm_with_tools = llm.bind_tools(tool_center.get_langchain_tools())
```

### 8.3 显式创建

```python
from graph_agent.llm import LLMFactory, LLMConfig

config = LLMConfig(
    provider="ollama",
    model="qwen2.5:7b",
    temperature=0.1,
)
llm_provider = LLMFactory.create(config)
```

## 9. 设计优势

1. **解耦** - LLM 实现与业务逻辑完全分离
2. **可插拔** - 更换 LLM 提供商只需修改环境变量
3. **可测试** - 可轻松 Mock LLM 实例进行单元测试
4. **可扩展** - 添加新提供商不影响现有代码
5. **统一配置** - 所有提供商使用相同的配置模式

## 10. 后续扩展方向

- [ ] 支持多 LLM 实例同时配置（路由、负载均衡）
- [ ] LLM 调用缓存和重试机制
- [ ] 调用指标收集与监控
- [ ] 支持流式和异步调用的统一封装
- [ ] LLM 响应格式标准化
- [ ] 自动降级和故障转移
