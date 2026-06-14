"""查询预处理模块。

支持三种预处理策略：
1. 查询改写 (Query Rewriting) — 将口语化查询转化为关键词查询
2. 查询分解 (Query Decomposition) — 将复杂问题分解为多个子查询
3. HyDE (假设文档嵌入) — 用假设答案的嵌入检索
"""

import logging
from typing import Any

from graph_agent.rag.rag_types import ProcessedQuery

logger = logging.getLogger(__name__)


class QueryProcessor:
    """查询预处理模块。

    将 Agent 的任务描述转化为更适合检索的查询形式。
    """

    def __init__(self, llm: Any, embedder: "BaseEmbedder"):
        """初始化查询处理器。

        Args:
            llm: LLM 实例（用于改写、分解、生成假设答案）。
            embedder: 嵌入服务（用于 HyDE 向量生成）。
        """
        self.llm = llm
        self.embedder = embedder

    async def process(
        self,
        query: str,
        strategies: list[str] | None = None,
    ) -> ProcessedQuery:
        """对原始查询进行预处理。

        Args:
            query: 原始查询文本（可能来自 Agent 的任务描述）。
            strategies: 启用的策略列表，默认 ["rewrite"]。
                       可选值: "rewrite", "decompose", "hyde"。

        Returns:
            ProcessedQuery 包含处理后的查询变体和元数据。
        """
        if strategies is None:
            strategies = ["rewrite"]

        result = ProcessedQuery(original=query, strategies_used=strategies)

        for strategy in strategies:
            try:
                if strategy == "rewrite":
                    result.rewritten = await self.rewrite(query)
                elif strategy == "decompose":
                    result.sub_queries = await self.decompose(query)
                elif strategy == "hyde":
                    result.hyde_vector = await self.hyde(query)
                else:
                    logger.warning("未知查询预处理策略: %s", strategy)
            except Exception as e:
                logger.error("查询预处理策略 %s 失败: %s", strategy, e)

        return result

    async def rewrite(self, query: str) -> str:
        """查询改写：将口语化/任务描述式的查询转化为关键词丰富的检索查询。

        示例：
        - 输入："帮我看看部署流程怎么弄"
        - 输出："部署 安装 配置 启动 环境变量 依赖"
        """
        if self.llm is None:
            logger.warning("LLM 未配置，跳过查询改写")
            return query

        prompt = f"""将以下任务描述转化为适合文档检索的关键词查询。
提取核心技术术语和关键概念，去除口语化表达。
只返回检索查询文本，不要包含其他内容。

任务描述: {query}
检索查询:"""

        try:
            response = await self.llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            rewritten = content.strip()
            if rewritten:
                logger.debug("查询改写: %s -> %s", query[:50], rewritten[:50])
                return rewritten
        except Exception as e:
            logger.error("查询改写失败: %s", e)

        return query

    async def decompose(self, query: str) -> list[str]:
        """查询分解：将复杂问题分解为多个子查询。

        示例：
        - 输入："GraphAgent 的上下文管理机制和记忆系统如何交互"
        - 输出：[
            "GraphAgent 上下文管理 架构",
            "记忆系统 记忆提取 存储 检索",
            "上下文注入 记忆注入 Agent 编排"
          ]
        """
        if self.llm is None:
            return [query]

        prompt = f"""将以下复杂问题分解为 2-4 个独立的检索子查询。
每个子查询应聚焦于问题的一个方面，使用关键词形式。
返回 JSON 数组格式: ["子查询1", "子查询2", ...]
只返回 JSON 数组，不要包含其他内容。

复杂问题: {query}"""

        try:
            import json
            import re
            response = await self.llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                sub_queries = json.loads(json_match.group())
                logger.debug("查询分解: %s -> %d 个子查询", query[:50], len(sub_queries))
                return sub_queries
        except Exception as e:
            logger.error("查询分解失败: %s", e)

        return [query]

    async def hyde(self, query: str) -> list[float]:
        """HyDE (Hypothetical Document Embeddings)。

        1. LLM 生成一个假设的答案文档
        2. 用假设答案的嵌入向量进行检索

        关键实现细节：
        假设答案本质上是模拟的"文档片段"（而非查询语句），
        因此必须调用 embedder.embed() 而非 embed_query()。
        对于区分 query/document 编码的模型（如 BGE 系列），
        错误的调用会导致嵌入偏向查询空间，反而降低检索质量。
        """
        if self.llm is None:
            logger.warning("LLM 未配置，使用原始查询作为 HyDE 回退")
            return self.embedder.embed_query(query)

        hypothetical_answer = await self._generate_hypothetical_answer(query)
        # 注意：使用 embed() 而非 embed_query()
        # 假设答案是一段模拟文档文本，应与文档使用相同编码
        embeddings = self.embedder.embed([hypothetical_answer])
        return embeddings[0]

    async def _generate_hypothetical_answer(self, query: str) -> str:
        """生成假设答案文档。"""
        prompt = f"""请根据以下问题，生成一段假设的答案文档（200-500字）。
这段文档应该像真实的技术文档一样，包含相关概念、架构描述和关键术语。

问题: {query}
假设答案文档:"""

        try:
            response = await self.llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            return content.strip()
        except Exception as e:
            logger.error("生成假设答案失败: %s", e)
            return query
