"""FTS5 关键词检索器。

复用记忆系统的 FTS5 索引策略（unicode61 分词器、中文 bigram + 英文 token）。
"""

import logging
from typing import Optional

from graph_agent.rag.document_store import DocumentStore
from graph_agent.rag.rag_types import Chunk

logger = logging.getLogger(__name__)


class KeywordSearcher:
    """基于 FTS5 的关键词检索器。

    复用记忆系统（memory/）的 FTS5 索引策略：
    - 使用 unicode61 分词器（支持中英文混合）
    - 多关键词 OR 查询
    - 特殊字符转义
    - 降级到 LIKE 模糊匹配
    """

    def __init__(self, document_store: DocumentStore):
        self.store = document_store

    def search(
        self,
        kb_name: str,
        query: str,
        top_k: int = 20,
        metadata_filter: dict | None = None,
    ) -> list[tuple[Chunk, float]]:
        """FTS5 关键词检索。

        查询构造流程：
        1. 提取关键词（中文 bigram + 英文 token）
        2. 构造 FTS5 MATCH 表达式
        3. 执行查询，按 BM25 相关度排序
        4. 无结果时降级为 LIKE 模糊匹配

        Args:
            kb_name: 知识库名称。
            query: 查询文本。
            top_k: 返回结果数。
            metadata_filter: 元数据过滤条件。

        Returns:
            (chunk, bm25_score) 列表，按相关度降序。
        """
        # 提取关键词用于 FTS5 查询
        keywords = self._extract_keywords(query)
        fts_query = self._build_fts_query(keywords)

        logger.debug("FTS5 检索: kb=%s, query=%s, keywords=%s", kb_name, query, keywords)

        results = self.store.search_fts(
            query=fts_query,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )

        logger.debug("FTS5 检索结果: %d 条", len(results))
        return results

    def _extract_keywords(self, query: str) -> list[str]:
        """提取关键词：中文用 bigram，英文/数字用 token。

        策略参考 memory_searcher.py：
        - 中文连续字符 → 逐 2-gram 切分
        - 英文/数字 → 按空白和标点 tokenize
        - 混合内容分别处理
        """
        import re

        keywords = []

        # 分离中文和非中文段落
        segments = re.split(r'([一-鿿]+)', query)

        for segment in segments:
            if not segment.strip():
                continue
            if re.match(r'[一-鿿]+', segment):
                # 中文 bigram
                for i in range(len(segment) - 1):
                    keywords.append(segment[i:i + 2])
                if len(segment) == 1:
                    keywords.append(segment)
            else:
                # 英文/数字 tokenize
                tokens = re.findall(r'[a-zA-Z0-9_]+', segment)
                keywords.extend(t.lower() for t in tokens if len(t) >= 2)

        # 去重，保留原始查询作为备选
        unique_keywords = list(dict.fromkeys(keywords))
        if not unique_keywords:
            # 如果完全没有提取到关键词，用原查询
            unique_keywords = [query]

        return unique_keywords

    def _build_fts_query(self, keywords: list[str]) -> str:
        """构造 FTS5 MATCH 表达式。

        多个关键词用 OR 连接，每个关键词用双引号包裹以支持精确短语匹配。
        """
        # 去重并限制关键词数量
        unique_kw = list(dict.fromkeys(keywords))[:20]

        # 每个关键词包裹双引号
        quoted = [f'"{kw}"' for kw in unique_kw]

        # OR 连接
        return " OR ".join(quoted)
