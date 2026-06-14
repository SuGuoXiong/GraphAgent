"""固定大小滑动窗口分块器。

将文本编码为 token 后按固定窗口滑动切分，
窗口之间有 chunk_overlap 个 token 的重叠。
"""

import logging

from .base_chunker import BaseChunker
from graph_agent.rag.rag_types import Chunk

logger = logging.getLogger(__name__)


class TokenChunker(BaseChunker):
    """固定大小滑动窗口分块策略。

    实现方式：
    1. 使用 tiktoken 将全文编码为 token 序列
    2. 以 chunk_size 为窗口大小、chunk_size - chunk_overlap 为步长滑动
    3. 每个窗口解码回文本作为一个 Chunk

    优点：速度最快，token 数量精确可控
    缺点：可能在词或句中间截断，语义完整性较差
    """

    def __init__(self, chunk_size: int = 512,
                 chunk_overlap: int = 50,
                 max_chunk_tokens: int = 8191):
        """初始化 TokenChunker。

        Args:
            chunk_size: 每个分块的目标 token 数
            chunk_overlap: 相邻分块之间重叠的 token 数
            max_chunk_tokens: 单个分块允许的最大 token 数
        """
        super().__init__(chunk_size, chunk_overlap, max_chunk_tokens)

    def chunk(self, text: str, metadata: dict) -> list[Chunk]:
        """对文本执行固定大小滑动窗口分块。

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

        # 编码全文为 token 列表
        tokens = self._encoder.encode(text)
        total_tokens = len(tokens)
        logger.debug(
            "TokenChunker: 全文 %d tokens，chunk_size=%d，chunk_overlap=%d",
            total_tokens, self.chunk_size, self.chunk_overlap,
        )

        # 计算有效步长
        step = max(1, self.chunk_size - self.chunk_overlap)

        chunks: list[Chunk] = []
        start = 0

        while start < total_tokens:
            end = min(start + self.chunk_size, total_tokens)
            window_tokens = tokens[start:end]

            # 解码窗口 token 为文本
            chunk_text = self._encoder.decode(window_tokens)

            # 构造 Chunk
            chunk = self._make_chunk(
                content=chunk_text,
                chunk_index=len(chunks),
                doc_id=doc_id,
                kb_name=kb_name,
                extra_metadata={
                    "token_start": start,
                    "token_end": end,
                },
            )
            chunks.append(chunk)

            # 到达末尾则退出
            if end >= total_tokens:
                break

            start += step

        # 设置相邻分块引用
        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.metadata["prev_chunk_id"] = chunks[i - 1].id
            if i < len(chunks) - 1:
                chunk.metadata["next_chunk_id"] = chunks[i + 1].id

        logger.info(
            "TokenChunker: 文本 %d tokens -> %d 个分块 (doc_id=%s)",
            total_tokens, len(chunks), doc_id,
        )
        return chunks
