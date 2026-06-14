"""语义分块器。

基于句子嵌入相似度检测语义边界，在语义转折处切分文本。
使用独立的轻量级嵌入模型以规避循环依赖。
"""

import logging
import re

from .base_chunker import BaseChunker
from graph_agent.rag.rag_types import Chunk

logger = logging.getLogger(__name__)


class SemanticChunker(BaseChunker):
    """语义分块策略。

    核心流程：
    1. 将文本按句子分割
    2. 使用 all-MiniLM-L6-v2 计算每个句子的嵌入向量
    3. 计算相邻句子之间的余弦相似度
    4. 找出所有相似度低于指定百分位阈值的"语义断点"
    5. 在断点处切分，形成语义连贯的分块
    6. 若某组分块超过 chunk_size，则进一步强制拆分

    注意：此分块器内部使用独立的 sentence-transformers 模型，
    不依赖于项目的 Embedder 模块，避免循环依赖。
    """

    # 句子分隔正则：中文和英文句子结束标点
    _SENTENCE_PATTERN = re.compile(
        r'(?<=[。！？.!?])\s*'
    )

    def __init__(self, chunk_size: int = 512,
                 chunk_overlap: int = 50,
                 max_chunk_tokens: int = 8191,
                 model_name: str = "all-MiniLM-L6-v2",
                 similarity_percentile: float = 90):
        """初始化语义分块器。

        Args:
            chunk_size: 目标分块大小（token 数）
            chunk_overlap: 相邻分块重叠的 token 数
            max_chunk_tokens: 单个分块允许的最大 token 数
            model_name: sentence-transformers 模型名称
            similarity_percentile: 语义断点检测用的百分位阈值
                （取值 0-100，越大则断点越少、分块越大）
        """
        super().__init__(chunk_size, chunk_overlap, max_chunk_tokens)
        self.model_name = model_name
        self.similarity_percentile = similarity_percentile
        self._model = None  # 延迟加载

    def _get_model(self):
        """延迟加载 sentence-transformers 模型。"""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                logger.info("加载语义分块模型: %s", self.model_name)
                self._model = SentenceTransformer(self.model_name)
            except ImportError:
                raise ImportError(
                    "SemanticChunker 需要 sentence-transformers 库。"
                    "请执行: pip install sentence-transformers"
                )
        return self._model

    def chunk(self, text: str, metadata: dict) -> list[Chunk]:
        """对文本执行语义分块。

        Args:
            text: 待分块的原始文本
            metadata: 须包含 doc_id 和 kb_name

        Returns:
            Chunk 列表，按 chunk_index 升序排列
        """
        doc_id = metadata.get("doc_id", "unknown")
        kb_name = metadata.get("kb_name", "unknown")

        if not text.strip():
            logger.debug("文本为空，返回空列表 (doc_id=%s)", doc_id)
            return []

        # 1. 分割句子
        sentences = self._split_sentences(text)
        if len(sentences) <= 1:
            # 单句文本直接作为一个分块
            chunk = self._make_chunk(
                content=text.strip(),
                chunk_index=0,
                doc_id=doc_id,
                kb_name=kb_name,
            )
            return [chunk]

        logger.debug("语义分块: 共 %d 个句子", len(sentences))

        # 2. 计算句子嵌入
        model = self._get_model()
        embeddings = model.encode(sentences, convert_to_tensor=True)

        # 3. 计算相邻句子相似度
        similarities = self._compute_adjacent_similarities(embeddings)

        # 4. 找出语义断点
        breakpoints = self._find_breakpoints(similarities)
        logger.debug("语义分块: 检测到 %d 个断点", len(breakpoints))

        # 5. 按断点分组句子
        groups = self._group_sentences(sentences, breakpoints)

        # 6. 对超长组强制拆分
        final_chunks_text = self._split_oversized_groups(groups)

        # 7. 构造 Chunk 对象
        chunks: list[Chunk] = []
        for i, chunk_text in enumerate(final_chunks_text):
            chunk = self._make_chunk(
                content=chunk_text,
                chunk_index=i,
                doc_id=doc_id,
                kb_name=kb_name,
                extra_metadata={"chunk_strategy": "semantic"},
            )
            chunks.append(chunk)

        # 设置相邻分块引用
        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.metadata["prev_chunk_id"] = chunks[i - 1].id
            if i < len(chunks) - 1:
                chunk.metadata["next_chunk_id"] = chunks[i + 1].id

        logger.info(
            "SemanticChunker: 文本 %d tokens -> %d 个分块 (doc_id=%s)",
            self._count_tokens(text), len(chunks), doc_id,
        )
        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """将文本分割为句子列表。

        支持中英文混合文本的句子边界检测。
        过滤掉空白句子。

        Args:
            text: 原始文本

        Returns:
            非空句子列表
        """
        raw_sentences = self._SENTENCE_PATTERN.split(text)
        # 过滤空白句子并去除首尾空白
        sentences = [s.strip() for s in raw_sentences if s.strip()]
        return sentences

    @staticmethod
    def _compute_adjacent_similarities(embeddings) -> list[float]:
        """计算相邻句子嵌入向量之间的余弦相似度。

        Args:
            embeddings: 句子嵌入张量 (n_sentences, dim)

        Returns:
            相邻相似度列表 (n_sentences - 1,)
        """
        from sentence_transformers import util

        similarities: list[float] = []
        for i in range(len(embeddings) - 1):
            sim = util.cos_sim(embeddings[i], embeddings[i + 1]).item()
            similarities.append(sim)
        return similarities

    def _find_breakpoints(self, similarities: list[float]) -> list[int]:
        """根据相似度百分位阈值找出语义断点。

        将阈值设定为 (100 - similarity_percentile) 分位数，
        相似度低于该阈值的相邻句对视为语义断点。
        例如 similarity_percentile=90 时，使用第 10 百分位作为阈值，
        即仅最不相似的 10% 成为断点。

        同时要求断点处的相似度是局部极小值（低于左右邻居），
        以避免在平滑下降区域产生过多断点。

        Args:
            similarities: 相邻句子相似度列表

        Returns:
            断点索引列表（断点在索引 i 和 i+1 之间）
        """
        if not similarities:
            return []

        import numpy as np

        # 计算百分位阈值
        percentile = 100.0 - self.similarity_percentile
        threshold = float(np.percentile(similarities, percentile))

        logger.debug(
            "语义分块断点检测: 百分位=%.1f%%, 阈值=%.4f, 相似度范围=[%.4f, %.4f]",
            percentile, threshold, min(similarities), max(similarities),
        )

        # 找出低于阈值且为局部极小值的点
        breakpoints: list[int] = []
        n = len(similarities)

        for i, sim in enumerate(similarities):
            if sim >= threshold:
                continue

            # 检查是否为局部极小值
            left_lower = (i > 0 and similarities[i - 1] <= sim)
            right_lower = (i < n - 1 and similarities[i + 1] <= sim)

            if not left_lower and not right_lower:
                breakpoints.append(i)

        return breakpoints

    def _group_sentences(self, sentences: list[str],
                         breakpoints: list[int]) -> list[str]:
        """根据断点将句子分组。

        Args:
            sentences: 句子列表
            breakpoints: 断点索引列表

        Returns:
            分组后的文本块列表
        """
        if not breakpoints:
            return [" ".join(sentences)]

        break_set = set(breakpoints)
        groups: list[str] = []
        current_group: list[str] = []

        for i, sentence in enumerate(sentences):
            current_group.append(sentence)

            # 在断点处结束当前组（即 sentence[i] 和 sentence[i+1] 之间断开）
            if i in break_set and i < len(sentences) - 1:
                groups.append(" ".join(current_group))
                current_group = []

        # 添加最后一组
        if current_group:
            groups.append(" ".join(current_group))

        return groups

    def _split_oversized_groups(self, groups: list[str]) -> list[str]:
        """对超过 chunk_size 的组进行强制拆分。

        Args:
            groups: 句子组文本列表

        Returns:
            拆分后的文本块列表
        """
        result: list[str] = []
        for group in groups:
            if self._count_tokens(group) <= self.chunk_size:
                result.append(group)
            else:
                # 按 token 强制切分
                tokens = self._encoder.encode(group)
                start = 0
                while start < len(tokens):
                    end = min(start + self.chunk_size, len(tokens))
                    piece = self._encoder.decode(tokens[start:end])
                    result.append(piece)
                    start = end
        return result
