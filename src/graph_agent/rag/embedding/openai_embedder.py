"""OpenAI 兼容嵌入服务。

通过 OpenAI 兼容 API 调用远程嵌入模型。
复用项目 LLM 环境变量（LLM_API_KEY、LLM_BASE_URL）。
支持自动重试（指数退避）。
"""

import logging
import os
import time

from .base_embedder import BaseEmbedder

logger = logging.getLogger(__name__)


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI 兼容 API 嵌入器。

    通过标准的 /v1/embeddings 端点调用嵌入服务，
    支持 OpenAI、Azure OpenAI 及任何兼容的第三方服务。

    默认使用 text-embedding-3-small 模型（1536 维）。
    """

    # 模型维度映射（常用模型）
    _DIMENSIONS: dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, model_name: str = "text-embedding-3-small",
                 api_key: str | None = None,
                 base_url: str | None = None,
                 batch_size: int = 32,
                 max_retries: int = 3):
        """初始化 OpenAI 兼容嵌入器。

        Args:
            model_name: 嵌入模型名称
            api_key: API 密钥，默认从 LLM_API_KEY 环境变量读取
            base_url: API 基础 URL，默认从 LLM_BASE_URL 环境变量读取
            batch_size: 批处理大小
            max_retries: 最大重试次数
        """
        super().__init__(model_name)
        # API 密钥优先级: 显式传入 > LLM_API_KEY > OPENAI_API_KEY
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL", "")
        self._batch_size = batch_size
        self.max_retries = max_retries

    def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本批次进行嵌入。

        自动分批处理，超长文本自动截断。
        失败时最多重试 3 次，使用指数退避策略。

        Args:
            texts: 待嵌入的文本列表

        Returns:
            嵌入向量列表，维度由模型决定

        Raises:
            RuntimeError: 所有重试均失败
        """
        if not texts:
            return []

        # 截断超长文本
        texts = self._truncate_texts(texts)

        all_embeddings: list[list[float]] = []
        batch_size = self.get_batch_size()

        for batch_start in range(0, len(texts), batch_size):
            batch = texts[batch_start:batch_start + batch_size]
            logger.debug(
                "嵌入批次: %d/%d 条文本，模型=%s",
                batch_start + 1, min(batch_start + batch_size, len(texts)),
                self.model_name,
            )

            for attempt in range(self.max_retries):
                try:
                    embeddings = self._call_api(batch)
                    all_embeddings.extend(embeddings)
                    break
                except Exception as e:
                    if attempt < self.max_retries - 1:
                        wait_time = 2 ** attempt  # 指数退避: 1s, 2s, 4s
                        logger.warning(
                            "嵌入请求失败 (尝试 %d/%d): %s，%d 秒后重试",
                            attempt + 1, self.max_retries, e, wait_time,
                        )
                        time.sleep(wait_time)
                    else:
                        logger.error(
                            "嵌入请求全部 %d 次尝试均失败: %s",
                            self.max_retries, e,
                        )
                        raise RuntimeError(
                            f"OpenAI 嵌入请求失败（{self.max_retries}次重试后）: {e}"
                        ) from e

        return all_embeddings

    def embed_query(self, query: str) -> list[float]:
        """对单个查询文本进行嵌入。

        Args:
            query: 查询文本

        Returns:
            查询嵌入向量
        """
        results = self.embed([query])
        if not results:
            raise RuntimeError("查询嵌入返回了空结果")
        return results[0]

    def get_dimension(self) -> int:
        """获取当前模型的嵌入向量维度。

        从内置维度映射表查找，若未知则通过一次 API 调用探测。
        """
        if self.model_name in self._DIMENSIONS:
            return self._DIMENSIONS[self.model_name]

        # 通过嵌入一个短文本探测维度
        try:
            result = self.embed(["dimension probe"])
            if result:
                return len(result[0])
        except Exception as e:
            logger.warning("无法探测嵌入维度: %s", e)

        return 0

    def get_batch_size(self) -> int:
        """获取批处理大小。"""
        return self._batch_size

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """调用 OpenAI 兼容的嵌入 API。

        Args:
            texts: 待嵌入的文本列表

        Returns:
            嵌入向量列表

        Raises:
            ImportError: 缺少 openai 库
            RuntimeError: API 调用失败
        """
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "OpenAIEmbedder 需要 openai 库。请执行: pip install openai"
            )

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = OpenAI(**client_kwargs)

        response = client.embeddings.create(
            model=self.model_name,
            input=texts,
        )

        # 按输入顺序提取嵌入向量
        embeddings = [item.embedding for item in response.data]

        logger.debug(
            "OpenAI 嵌入成功: %d 条文本，模型=%s",
            len(embeddings), self.model_name,
        )
        return embeddings
