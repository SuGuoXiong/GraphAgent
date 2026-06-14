"""Markdown 感知分块器。

在递归分块的基础上增加 Markdown 结构感知：
- 保护代码块不被截断
- 保护表格不被截断
- 将标题路径附加到每个分块前面
"""

import logging
import re

from .recursive_chunker import RecursiveChunker
from graph_agent.rag.rag_types import Chunk

logger = logging.getLogger(__name__)


class MarkdownAwareChunker(RecursiveChunker):
    """Markdown 感知递归分块策略。

    继承 RecursiveChunker，增加了三项 Markdown 结构保护：
    1. 代码块完整性 —— 被 ``` 包围的代码块不会被分割
    2. 表格完整性 —— 管道符表格行不会被分割
    3. 标题路径 —— 每个分块前附加当前章节路径（如 "# 概述 > ## 背景"）

    实现原理：
    - 预处理阶段：提取代码块和表格，用占位符替换
    - 分割阶段：对替换后的文本执行递归分割
    - 后处理阶段：还原占位符、附加标题路径
    """

    # 代码块正则（匹配 ``` 包围的代码块，含语言标识符）
    _CODE_BLOCK_RE = re.compile(
        r'```[^\n]*\n.*?```',
        re.DOTALL,
    )

    # 表格行正则（管道符开头和结尾的行）
    _TABLE_ROW_RE = re.compile(
        r'^\|.+\|$',
        re.MULTILINE,
    )

    # 表格块正则（连续的管道符行，含分隔行）
    _TABLE_BLOCK_RE = re.compile(
        r'(?:^\|.+\|$\n?)+',
        re.MULTILINE,
    )

    # Markdown 标题正则
    _HEADING_RE = re.compile(
        r'^(#{1,6})\s+(.+)$',
        re.MULTILINE,
    )

    def __init__(self, chunk_size: int = 512,
                 chunk_overlap: int = 50,
                 max_chunk_tokens: int = 8191,
                 separators: list[str] | None = None,
                 prepend_heading: bool = True):
        """初始化 Markdown 感知分块器。

        Args:
            chunk_size: 目标分块大小（token 数）
            chunk_overlap: 相邻分块重叠的 token 数
            max_chunk_tokens: 单个分块允许的最大 token 数
            separators: 自定义分隔符优先级列表
            prepend_heading: 是否在每个分块前附加标题路径
        """
        super().__init__(chunk_size, chunk_overlap, max_chunk_tokens, separators)
        self.prepend_heading = prepend_heading

    def chunk(self, text: str, metadata: dict) -> list[Chunk]:
        """对 Markdown 文本执行结构感知分块。

        Args:
            text: 待分块的 Markdown 文本
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
            "MarkdownAwareChunker: 全文 %d tokens，chunk_size=%d",
            total_tokens, self.chunk_size,
        )

        # 1. 构建标题路径映射（行号 -> 标题路径）
        heading_map = self._build_heading_map(text)

        # 2. 提取并保护代码块
        protected_text, code_blocks = self._extract_code_blocks(text)

        # 3. 提取并保护表格
        protected_text, table_blocks = self._extract_tables(protected_text)

        # 4. 递归分割
        splits = self._split_text(protected_text, self.separators.copy())
        merged = self._merge_splits(splits)

        # 5. 还原代码块和表格
        restored = []
        for chunk_text in merged:
            chunk_text = self._restore_placeholders(chunk_text, table_blocks)
            chunk_text = self._restore_placeholders(chunk_text, code_blocks)
            restored.append(chunk_text)

        # 6. 附加标题路径
        if self.prepend_heading:
            restored = [
                self._prepend_heading_path(chunk_text, heading_map)
                for chunk_text in restored
            ]

        # 7. 构造 Chunk 对象
        chunks: list[Chunk] = []
        for i, content in enumerate(restored):
            chunk = self._make_chunk(
                content=content,
                chunk_index=i,
                doc_id=doc_id,
                kb_name=kb_name,
                extra_metadata={
                    "chunk_strategy": "markdown",
                    "heading_path": self._find_relevant_heading(content, heading_map),
                },
            )
            chunks.append(chunk)

        # 设置相邻分块引用
        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.metadata["prev_chunk_id"] = chunks[i - 1].id
            if i < len(chunks) - 1:
                chunk.metadata["next_chunk_id"] = chunks[i + 1].id

        logger.info(
            "MarkdownAwareChunker: 文本 %d tokens -> %d 个分块 (doc_id=%s)",
            total_tokens, len(chunks), doc_id,
        )
        return chunks

    # ------------------------------------------------------------------
    # 代码块保护
    # ------------------------------------------------------------------

    def _extract_code_blocks(self, text: str) -> tuple[str, dict[str, str]]:
        """提取代码块并用占位符替换。

        Args:
            text: 原始 Markdown 文本

        Returns:
            (替换后的文本, {占位符: 原始代码块})
        """
        blocks: dict[str, str] = {}
        counter = [0]  # 使用列表以实现闭包可变

        def _replace(match: re.Match) -> str:
            placeholder = f"__CODE_BLOCK_{counter[0]:04d}__"
            blocks[placeholder] = match.group(0)
            counter[0] += 1
            return placeholder

        protected = self._CODE_BLOCK_RE.sub(_replace, text)
        logger.debug("提取 %d 个代码块", len(blocks))
        return protected, blocks

    # ------------------------------------------------------------------
    # 表格保护
    # ------------------------------------------------------------------

    def _extract_tables(self, text: str) -> tuple[str, dict[str, str]]:
        """提取表格块并用占位符替换。

        识别连续的管道符行（含分隔行如 |---|---| ）作为一个表格块。

        Args:
            text: 待处理的文本（可能已包含代码块占位符）

        Returns:
            (替换后的文本, {占位符: 原始表格块})
        """
        blocks: dict[str, str] = {}
        counter = [0]

        def _replace(match: re.Match) -> str:
            placeholder = f"__TABLE_BLOCK_{counter[0]:04d}__"
            blocks[placeholder] = match.group(0)
            counter[0] += 1
            return placeholder

        protected = self._TABLE_BLOCK_RE.sub(_replace, text)
        logger.debug("提取 %d 个表格块", len(blocks))
        return protected, blocks

    # ------------------------------------------------------------------
    # 标题路径
    # ------------------------------------------------------------------

    def _build_heading_map(self, text: str) -> dict[int, str]:
        """解析文本中的标题，构建行号到标题路径的映射。

        标题路径形如 "# 概述 > ## 背景 > ### 问题描述"，
        表示从根标题到当前位置的层级路径。

        Args:
            text: 原始 Markdown 文本

        Returns:
            {行号: 标题路径字符串}
        """
        heading_map: dict[int, str] = {}
        heading_stack: list[tuple[int, str]] = []  # [(level, heading_text)]

        lines = text.split("\n")
        for line_num, line in enumerate(lines):
            match = self._HEADING_RE.match(line)
            if not match:
                continue

            hashes = match.group(1)
            heading_text = match.group(2).strip()
            level = len(hashes)

            # 弹出比当前层级更深或同级的标题
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()

            heading_stack.append((level, heading_text))

            # 构建标题路径
            path_parts = [
                f"{'#' * lvl} {txt}"
                for lvl, txt in heading_stack
            ]
            heading_map[line_num] = " > ".join(path_parts)

        return heading_map

    def _find_relevant_heading(self, chunk_text: str,
                               heading_map: dict[int, str]) -> str:
        """找到与分块内容最相关的标题路径。

        策略：取 heading_map 中的最后一个标题路径作为该分块的上下文。
        因为 heading_map 按行号递增构建，最后一个即为最近遇到的标题。

        Args:
            chunk_text: 分块文本（可能含占位符）
            heading_map: 标题路径映射

        Returns:
            标题路径字符串，若不存在则返回空字符串
        """
        if not heading_map:
            return ""

        # 返回最深层的标题路径（字典最后一个条目）
        last_key = max(heading_map.keys())
        return heading_map[last_key]

    def _prepend_heading_path(self, chunk_text: str,
                              heading_map: dict[int, str]) -> str:
        """在分块文本前附加相关的标题路径。

        Args:
            chunk_text: 分块文本
            heading_map: 标题路径映射

        Returns:
            附加了标题路径的文本
        """
        heading = self._find_relevant_heading(chunk_text, heading_map)
        if heading:
            return f"**上下文路径:** {heading}\n\n{chunk_text}"
        return chunk_text

    # ------------------------------------------------------------------
    # 占位符还原
    # ------------------------------------------------------------------

    @staticmethod
    def _restore_placeholders(text: str, blocks: dict[str, str]) -> str:
        """将文本中的占位符还原为原始内容。

        Args:
            text: 含占位符的文本
            blocks: {占位符: 原始内容} 映射

        Returns:
            还原后的文本
        """
        for placeholder, original in blocks.items():
            text = text.replace(placeholder, original)
        return text
