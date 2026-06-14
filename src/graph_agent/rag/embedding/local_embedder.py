"""本地嵌入服务。

使用 sentence-transformers 库加载本地嵌入模型，
支持 BGE 模型的指令前缀（查询时添加 "为这个句子生成表示以用于检索相关文章："）。
"""

import logging

from .base_embedder import BaseEmbedder

logger = logging.getLogger(__name__)


class LocalEmbedder(BaseEmbedder):
    """本地 sentence-transformers 嵌入器。

    默认使用 BAAI/bge-small-zh-v1.5 模型（512 维），
    擅长中英文检索任务。

    BGE 模型对查询和文档使用不同的嵌入策略：
    - embed_query() 添加 BGE 查询指令前缀
    - embed() 不做任何前缀处理
    """

    # BGE 模型的查询指令前缀
    BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5",
                 device: str | None = None,
                 batch_size: int = 32,
                 query_prefix: str | None = None):
        """初始化本地嵌入器。

        Args:
            model_name: HuggingFace 模型名称或本地路径
            device: 推理设备（"cpu", "cuda", "cuda:0" 等），默认自动选择
            batch_size: 批处理大小
            query_prefix: 查询指令前缀，None 时使用 BGE_QUERY_PREFIX
        """
        super().__init__(model_name)
        self._batch_size = batch_size
        self.query_prefix = query_prefix if query_prefix is not None else self.BGE_QUERY_PREFIX
        self._model = None
        self._device = device

    def _get_model(self):
        """延迟加载 sentence-transformers 模型。

        模型在首次调用 embed/embed_query 时才加载，
        避免启动时的初始化开销和导入错误。
        """
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "LocalEmbedder 需要 sentence-transformers 库。"
                    "请执行: pip install sentence-transformers"
                )

            logger.info("加载本地嵌入模型: %s (device=%s)", self.model_name,
                        self._device or "auto")
            self._model = SentenceTransformer(
                self.model_name,
                device=self._device,
            )
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本批次进行嵌入。

        不对文本添加指令前缀，适合嵌入文档内容。

        Args:
            texts: 待嵌入的文本列表

        Returns:
            嵌入向量列表
        """
        if not texts:
            return []

        model = self._get_model()
        texts = self._truncate_texts(texts)

        logger.debug("本地嵌入: %d 条文本，模型=%s", len(texts), self.model_name)

        embeddings = model.encode(
            texts,
            batch_size=self.get_batch_size(),
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

        # 转换为 list[list[float]]
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        """对单个查询文本进行嵌入。

        添加 BGE 查询指令前缀以提升检索效果。

        Args:
            query: 查询文本

        Returns:
            查询嵌入向量
        """
        if self.query_prefix and not query.startswith(self.query_prefix):
            query = self.query_prefix + query
            logger.debug("查询添加 BGE 前缀: %s...", self.query_prefix[:30])

        results = self.embed([query])
        if not results:
            raise RuntimeError("查询嵌入返回了空结果")
        return results[0]

    def get_dimension(self) -> int:
        """获取嵌入向量的维度。"""
        model = self._get_model()
        return model.get_sentence_embedding_dimension()

    def get_batch_size(self) -> int:
        """获取批处理大小。"""
        return self._batch_size
