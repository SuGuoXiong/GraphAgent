"""递归分块器。

仿 LangChain 的递归文本分割策略：
按分隔符优先级依次尝试分割，超长片段递推到下一级分隔符。
"""

import logging

from .base_chunker import BaseChunker
from graph_agent.rag.rag_types import Chunk

logger = logging.getLogger(__name__)


class RecursiveChunker(BaseChunker):
    """递归分块策略。

    核心思想：
    1. 维护一个分隔符优先级列表
    2. 从最高优先级分隔符开始尝试分割文本
    3. 若某片段仍大于 chunk_size，使用下一级分隔符递归分割
    4. 若所有分隔符用尽仍超长，则按 chunk_size 强制截断
    5. 最后将相邻的短片段合并，使每个分块尽量接近 chunk_size

    分隔符优先级（从高到低）：
    "\\n\\n" > "\\n" > "。" > "！" > "？" > "." > "!" > "?" > "；" > ";" > " " > ""
    """

    # 分隔符优先级列表
    SEPARATORS: list[str] = [
        "\n\n", "\n",
        "。", "！", "？",
        ".", "!", "?",
        "；", ";",
        " ", "",
    ]

    def __init__(self, chunk_size: int = 512,
                 chunk_overlap: int = 50,
                 max_chunk_tokens: int = 8191,
                 separators: list[str] | None = None):
        """初始化递归分块器。

        Args:
            chunk_size: 目标分块大小（token 数）
            chunk_overlap: 相邻分块重叠的 token 数
            max_chunk_tokens: 单个分块允许的最大 token 数
            separators: 自定义分隔符优先级列表，默认使用 SEPARATORS
        """
        super().__init__(chunk_size, chunk_overlap, max_chunk_tokens)
        self.separators = separators if separators is not None else list(self.SEPARATORS)

    def chunk(self, text: str, metadata: dict) -> list[Chunk]:
        """对文本执行递归分块。

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

        total_tokens = self._count_tokens(text)
        logger.debug(
            "RecursiveChunker: 全文 %d tokens，chunk_size=%d",
            total_tokens, self.chunk_size,
        )

        # 递归分割
        splits = self._split_text(text, self.separators.copy())

        # 合并过短的片段
        merged = self._merge_splits(splits)

        # 构造 Chunk 列表
        chunks: list[Chunk] = []
        for i, content in enumerate(merged):
            chunk = self._make_chunk(
                content=content,
                chunk_index=i,
                doc_id=doc_id,
                kb_name=kb_name,
            )
            chunks.append(chunk)

        # 设置相邻分块引用
        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.metadata["prev_chunk_id"] = chunks[i - 1].id
            if i < len(chunks) - 1:
                chunk.metadata["next_chunk_id"] = chunks[i + 1].id

        logger.info(
            "RecursiveChunker: 文本 %d tokens -> %d 个分块 (doc_id=%s)",
            total_tokens, len(chunks), doc_id,
        )
        return chunks

    def _split_text(self, text: str, separators: list[str]) -> list[str]:
        """递归分割文本。

        Args:
            text: 待分割的文本
            separators: 当前可用的分隔符列表（从当前优先级开始）

        Returns:
            分割后的文本片段列表
        """
        # 获取当前最高优先级分隔符
        separator = separators[0]
        remaining_separators = separators[1:]

        # 用当前分隔符切分
        if separator:
            splits = text.split(separator)
        else:
            # 分隔符为空字符串时，按字符切分
            splits = list(text)

        # 处理每个片段
        result: list[str] = []
        for split in splits:
            if not split:
                continue

            if self._count_tokens(split) <= self.chunk_size:
                # 片段大小符合要求，直接加入结果
                result.append(split)
            elif remaining_separators:
                # 片段太大，使用下一级分隔符递归分割
                sub_splits = self._split_text(split, remaining_separators)
                result.extend(sub_splits)
            else:
                # 所有分隔符已用完，强制按 chunk_size 截断
                result.extend(self._force_split(split))

        return result

    def _force_split(self, text: str) -> list[str]:
        """当所有分隔符耗尽时，按 chunk_size 强制截断。

        Args:
            text: 超长文本

        Returns:
            强制截断后的文本片段列表
        """
        tokens = self._encoder.encode(text)
        total = len(tokens)

        if total <= self.chunk_size:
            return [text]

        pieces: list[str] = []
        start = 0
        while start < total:
            end = min(start + self.chunk_size, total)
            piece = self._encoder.decode(tokens[start:end])
            pieces.append(piece)
            start = end

        logger.debug("强制截断: %d tokens -> %d 个片段", total, len(pieces))
        return pieces

    def _merge_splits(self, splits: list[str]) -> list[str]:
        """合并过短的相邻片段，使每个分块尽量接近 chunk_size。

        使用贪心策略：从第一个片段开始累积，直到加入下一个片段会超出
        chunk_size 为止，然后将累积的片段作为一个分块。

        Args:
            splits: 待合并的文本片段列表

        Returns:
            合并后的文本片段列表
        """
        if not splits:
            return []

        merged: list[str] = []
        current = splits[0]

        for next_split in splits[1:]:
            combined = current + next_split
            if self._count_tokens(combined) <= self.chunk_size:
                current = combined
            else:
                merged.append(current)
                current = next_split

        # 添加最后一个累积片段
        merged.append(current)

        logger.debug("合并: %d 个片段 -> %d 个片段", len(splits), len(merged))
        return merged
