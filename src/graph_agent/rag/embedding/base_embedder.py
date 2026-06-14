"""嵌入器抽象基类。

定义所有嵌入服务必须实现的接口。
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseEmbedder(ABC):
    """嵌入器抽象基类。

    所有嵌入提供者（OpenAI、本地模型等）均继承此类。
    子类必须实现 embed()、embed_query()、get_dimension() 方法。
    """

    def __init__(self, model_name: str = ""):
        """初始化嵌入器。

        Args:
            model_name: 模型名称
        """
        self.model_name = model_name

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本批次进行嵌入。

        Args:
            texts: 待嵌入的文本列表

        Returns:
            嵌入向量列表，每个向量为 float 列表，顺序与 texts 一致
        """
        ...

    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """对单个查询文本进行嵌入。

        某些模型（如 BGE）对查询和文档使用不同的嵌入策略，
        此方法允许子类添加查询特有的前缀或处理。

        Args:
            query: 查询文本

        Returns:
            查询嵌入向量
        """
        ...

    @abstractmethod
    def get_dimension(self) -> int:
        """获取嵌入向量的维度。

        用于在向量存储初始化时确定索引维度。

        Returns:
            嵌入向量维度
        """
        ...

    def get_batch_size(self) -> int:
        """获取推荐的批处理大小。

        Returns:
            单次嵌入调用建议的文本数量上限
        """
        return 32

    def get_max_input_tokens(self) -> int:
        """获取单条文本允许的最大输入 token 数。

        Returns:
            最大 token 数
        """
        return 8191

    def _truncate_texts(self, texts: list[str]) -> list[str]:
        """将超长文本截断到 max_input_tokens 以内。

        使用 tiktoken cl100k_base 编码器进行截断。
        注意：此截断为简单的前缀截断，不保证语义完整性。

        Args:
            texts: 待截断的文本列表

        Returns:
            截断后的文本列表
        """
        import tiktoken

        max_tokens = self.get_max_input_tokens()
        encoder = tiktoken.get_encoding("cl100k_base")
        truncated = []

        for text in texts:
            tokens = encoder.encode(text)
            if len(tokens) <= max_tokens:
                truncated.append(text)
            else:
                truncated.append(encoder.decode(tokens[:max_tokens]))
                logger.warning(
                    "文本被截断: %d -> %d tokens（前50字符: %s...）",
                    len(tokens), max_tokens, text[:50],
                )

        return truncated
