"""主题记忆检索器 —— 关键词匹配 + 时间衰减排序。

未来升级为向量检索时，仅替换此模块内部实现，
MemoryRetriever.search() 接口保持不变。
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from graph_agent.memory.memory_store import MemoryStore
from graph_agent.memory.memory_types import SearchResult

# 半衰期（天）
_HALF_LIFE_DAYS = 30
_LAMBDA = math.log(2) / _HALF_LIFE_DAYS


class MemoryRetriever:
    """主题记忆检索器。"""

    def __init__(self, store: MemoryStore):
        self._store = store

    def search(self, intent: str, top_k: int = 5) -> list[SearchResult]:
        """根据意图描述检索最相关的主题记忆条目。

        Args:
            intent: GuardAgent 分析出的意图描述
            top_k: 返回结果数量上限

        Returns:
            按检索分降序排列的 SearchResult 列表
        """
        index = self._store.load_index()
        if not index.topics:
            return []

        query_keywords = _extract_keywords(intent)
        if not query_keywords:
            return []

        scored: list[tuple[float, SearchResult]] = []

        for topic_idx in index.topics:
            topic = self._store.load_topic(topic_idx.slug)
            if not topic or not topic.entries:
                continue

            # 计算 topic 级别的关键词匹配分
            topic_score = _keyword_match_score(query_keywords, topic_idx.keywords)

            for entry in topic.entries:
                # 条目级别的关键词匹配
                entry_text = f"{entry.title} {entry.task} {entry.approach} {entry.lessons}"
                entry_score = _keyword_match_score(query_keywords, _extract_keywords(entry_text))

                # 综合分数：topic 匹配 + entry 匹配
                combined = 0.3 * topic_score + 0.7 * entry_score

                # 时间衰减
                days = _days_since(entry.date)
                decay = math.exp(-_LAMBDA * days)
                final_score = combined * decay

                if final_score > 0:
                    scored.append((final_score, SearchResult(
                        slug=topic.slug,
                        title=topic.title,
                        entry=entry,
                        score=round(final_score, 3),
                    )))

        # 按分数降序排序
        scored.sort(key=lambda x: x[0], reverse=True)

        # 更新 last_accessed_at
        seen_slugs: set[str] = set()
        for _, result in scored[:top_k]:
            if result.slug not in seen_slugs:
                self._store.touch_topic_access(result.slug)
                seen_slugs.add(result.slug)

        return [r for _, r in scored[:top_k]]

    def search_by_keywords(self, keywords: list[str], top_k: int = 5) -> list[SearchResult]:
        """直接使用关键词列表检索（用于记忆合并时的主题匹配）。"""
        if not keywords:
            return []
        intent = " ".join(keywords)
        return self.search(intent, top_k=top_k)


def _extract_keywords(text: str) -> list[str]:
    """从文本中提取关键词（中英文混合）。"""
    keywords: list[str] = []
    # 提取中文词（连续中文字符组成的关键词片段）
    chinese_words = re.findall(r'[一-鿿]{2,}', text)
    keywords.extend(chinese_words)
    # 提取英文单词
    english_words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    keywords.extend(english_words)
    return keywords


def _keyword_match_score(query_keywords: list[str], target_keywords: list[str]) -> float:
    """计算关键词匹配分数（0-1）。"""
    if not query_keywords or not target_keywords:
        return 0.0
    query_lower = [k.lower() for k in query_keywords]
    target_lower = [k.lower() for k in target_keywords]
    matched = 0
    for qk in query_lower:
        for tk in target_lower:
            if qk in tk or tk in qk:
                matched += 1
                break
    return matched / len(query_lower)


def _days_since(date_str: str) -> float:
    """计算从给定日期到今天的天数。"""
    try:
        d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - d).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return 365.0  # 解析失败视为很久以前
