"""分块器抽象基类。

提供所有分块策略的公共功能：token 计数、分块大小校验。
"""

import logging
from abc import ABC, abstractmethod

import tiktoken

from graph_agent.rag.rag_types import Chunk

logger = logging.getLogger(__name__)


class BaseChunker(ABC):
    """分块器抽象基类。

    所有分块策略（Token、Recursive、Semantic 等）均继承此类。
    子类必须实现 chunk() 方法。
    """

    _encoding_name = "cl100k_base"

    def __init__(self, chunk_size: int = 512,
                 chunk_overlap: int = 50,
                 max_chunk_tokens: int = 8191):
        """初始化分块器。

        Args:
            chunk_size: 目标分块大小（token 数）
            chunk_overlap: 相邻分块重叠的 token 数
            max_chunk_tokens: 单个分块允许的最大 token 数，超限时发出警告
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_chunk_tokens = max_chunk_tokens
        self._encoder = tiktoken.get_encoding(self._encoding_name)

    @abstractmethod
    def chunk(self, text: str, metadata: dict) -> list[Chunk]:
        """将文本分割为 Chunk 列表。

        Args:
            text: 待分块的原始文本
            metadata: 元数据字典，需包含 doc_id 和 kb_name 用于生成 chunk ID

        Returns:
            Chunk 对象列表，按 chunk_index 升序排列
        """
        ...

    def _count_tokens(self, text: str) -> int:
        """计算文本的 token 数量。

        使用 tiktoken cl100k_base 编码器统计 token 数。
        注意：此方法返回精确 token 数，不同于 LLM 提交流程中的截断逻辑。
        """
        return len(self._encoder.encode(text))

    def _validate_chunk_size(self, chunk: Chunk) -> bool:
        """校验分块大小是否在允许范围内。

        若分块的 token_count 超过 max_chunk_tokens，记录警告日志。

        Args:
            chunk: 待校验的分块对象

        Returns:
            大小合规时返回 True，超限时返回 False
        """
        if chunk.token_count > self.max_chunk_tokens:
            logger.warning(
                "分块 %s 包含 %d tokens，超过上限 %d（内容前100字符: %s...）",
                chunk.id, chunk.token_count, self.max_chunk_tokens,
                chunk.content[:100]
            )
            return False
        return True

    def _make_chunk(self, content: str, chunk_index: int,
                    doc_id: str, kb_name: str,
                    extra_metadata: dict | None = None) -> Chunk:
        """构造 Chunk 对象的便捷方法。

        自动计算 token_count 和 content_hash，生成规范化的 chunk_id。

        Args:
            content: 分块文本内容
            chunk_index: 分块序号（从 0 开始）
            doc_id: 所属文档 ID
            kb_name: 所属知识库名称
            extra_metadata: 附加元数据（与原始 metadata 合并）

        Returns:
            构造好的 Chunk 对象
        """
        import hashlib

        chunk_id = f"{doc_id}_chunk_{chunk_index:04d}"
        token_count = self._count_tokens(content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        chunk_metadata = {}
        if extra_metadata:
            chunk_metadata.update(extra_metadata)

        chunk = Chunk(
            id=chunk_id,
            doc_id=doc_id,
            kb_name=kb_name,
            content=content,
            chunk_index=chunk_index,
            token_count=token_count,
            content_hash=content_hash,
            embedding=None,
            metadata=chunk_metadata,
        )

        self._validate_chunk_size(chunk)
        return chunk
