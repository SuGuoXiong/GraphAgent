"""检索结果重排序器。

支持两种重排序策略：
1. LLM-based Reranker（默认）— 复用 GraphAgent 现有 LLM
2. Cross-encoder Reranker（可选）— 更精确但需额外模型

Prompt Injection 防御：chunk 内容包裹在 markdown fence 中，添加显式标记。
"""

import json
import logging
import re
from typing import Any, Optional

from graph_agent.rag.rag_types import Chunk, SearchResult

logger = logging.getLogger(__name__)


class Reranker:
    """检索结果重排序器。

    两阶段重排序策略：
    1. LLM-based Reranker（默认）— 复用 GraphAgent 现有 LLM
    2. Cross-encoder Reranker（可选）— 更精确但需额外模型
    """

    def __init__(
        self,
        method: str = "llm",
        llm: Any = None,
        cross_encoder_model: str = "BAAI/bge-reranker-v2-m3",
    ):
        """初始化重排序器。

        Args:
            method: 重排序方法，"llm" 或 "cross_encoder"。
            llm: LLM 实例（method="llm" 时需要）。
            cross_encoder_model: Cross-encoder 模型名（method="cross_encoder" 时需要）。
        """
        self.method = method
        self.llm = llm
        self.cross_encoder_model = cross_encoder_model
        self._cross_encoder = None

    async def rerank(
        self,
        query: str,
        chunks: list[Chunk],
        top_k: int = 5,
    ) -> list[tuple[Chunk, float]]:
        """对检索结果进行重排序。

        Args:
            query: 查询文本。
            chunks: 待重排序的 chunk 列表（通常来自融合后的 top-N 候选）。
            top_k: 返回的最大结果数。

        Returns:
            (chunk, rerank_score) 列表，按 rerank_score 降序。
        """
        if not chunks:
            return []

        if len(chunks) <= top_k:
            # 候选数量已经 ≤ top_k，直接返回原始顺序
            return [(chunk, 1.0) for chunk in chunks]

        if self.method == "llm":
            scores = await self._llm_rerank(query, chunks)
        elif self.method == "cross_encoder":
            scores = await self._cross_encoder_rerank(query, chunks)
        else:
            logger.warning("未知重排序方法 %s，返回原始顺序", self.method)
            return [(chunk, 1.0) for chunk in chunks[:top_k]]

        # 组合并排序
        scored = list(zip(chunks, scores))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    async def _llm_rerank(
        self, query: str, chunks: list[Chunk]
    ) -> list[float]:
        """使用 LLM 重排序。

        Prompt 模板将每个 chunk 包裹在 markdown code fence 中，
        防止恶意文档内容通过 prompt injection 操控 LLM 判断。
        """
        if self.llm is None:
            logger.warning("LLM 未配置，无法执行 LLM 重排序")
            return [1.0] * len(chunks)

        # 构建 prompt（sanitize chunk 内容）
        chunk_texts = []
        for i, chunk in enumerate(chunks[:20]):  # 最多重排序 20 个候选
            # Prompt Injection 防御：包裹在 fence 中 + 显式标记
            sanitized = self._sanitize_chunk_content(chunk.content)
            chunk_texts.append(
                f"```\n[文档片段 {i}] 来源: {chunk.metadata.get('source_file', 'unknown')}\n"
                f"{sanitized}\n```"
            )

        prompt = f"""请评估以下文档片段与查询的相关性（0-10分，10=完全相关，0=无关）。

查询: {query}

以下为待评估文档片段：
{chr(10).join(chunk_texts)}

请返回 JSON 数组，包含每个片段的评分，格式: [分数0, 分数1, ...]
只返回 JSON 数组，不要包含其他内容。"""

        try:
            response = await self.llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)

            # 提取 JSON 数组
            json_match = re.search(r'\[[\d,\s.]+\]', content)
            if json_match:
                scores = json.loads(json_match.group())
                # 归一化到 [0, 1]
                max_s = max(scores) if scores else 1
                return [s / max_s for s in scores]
            else:
                logger.warning("无法从 LLM 响应中解析评分: %s", content[:200])
                return [1.0] * len(chunks)

        except Exception as e:
            logger.error("LLM 重排序失败: %s", e)
            # 降级：返回原始顺序
            return [(len(chunks) - i) / len(chunks) for i in range(len(chunks))]

    async def _cross_encoder_rerank(
        self, query: str, chunks: list[Chunk]
    ) -> list[float]:
        """使用 Cross-encoder 模型重排序。"""
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            logger.warning("sentence-transformers 未安装，无法使用 cross-encoder 重排序")
            return [1.0] * len(chunks)

        if self._cross_encoder is None:
            self._cross_encoder = CrossEncoder(self.cross_encoder_model)

        pairs = [(query, chunk.content) for chunk in chunks]
        scores = self._cross_encoder.predict(pairs)

        # scores 是 logits 或相似度，确保为 list[float]
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        return [float(s) for s in scores]

    @staticmethod
    def _sanitize_chunk_content(content: str) -> str:
        """清洗 chunk 内容，防止 prompt injection。

        策略：
        - 限制最大长度（500 字符）
        - 移除可能被解析为指令的模式
        """
        # 限制长度
        if len(content) > 500:
            content = content[:500] + "..."

        # 移除常见的 prompt injection 模式
        dangerous_patterns = [
            r'忽略.*指令',
            r'ignore.*instructions',
            r'system.*prompt',
            r'\[.*system.*\]',
        ]
        for pattern in dangerous_patterns:
            content = re.sub(pattern, '[FILTERED]', content, flags=re.IGNORECASE)

        return content
