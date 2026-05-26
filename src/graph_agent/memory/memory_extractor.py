"""记忆提取器 —— 从对话历史中提取用户画像和主题记忆。

作为后台异步任务执行，不阻塞用户交互。
使用 LLM 语义理解能力提取结构化记忆数据。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

from graph_agent.memory.memory_store import MemoryStore
from graph_agent.memory.memory_types import (
    UserProfile,
    UserPreferenceItem,
    TopicMemory,
    TopicMemoryEntry,
    _now,
)

if TYPE_CHECKING:
    from graph_agent.message import MessageBlock

logger = logging.getLogger(__name__)


class MemoryExtractor:
    """记忆提取器 —— 使用 LLM 从对话历史中提取记忆。

    设计为无状态：每次调用独立执行，产物由 MemoryManager 合并。
    """

    def __init__(self, store: MemoryStore):
        self._store = store

    async def extract(
        self,
        messages: list,
        task_summary: str = "",
        user_feedback: str = "",
    ) -> dict:
        """从本轮对话中提取用户画像增量和主题记忆增量。

        Args:
            messages: 本轮对话的消息列表（用户消息 + Agent 回复）
            task_summary: 本轮任务的简要描述（e.g. "代码分析"）
            user_feedback: 用户的反馈（如有明确表态）

        Returns:
            {"profile_increments": [...], "topic_memory": {...} | None}
        """
        extracted_text = self._build_extraction_text(messages)

        profile_increments = await self._extract_profile(extracted_text)
        topic_memory = await self._extract_topic(extracted_text, task_summary, user_feedback)

        return {
            "profile_increments": profile_increments,
            "topic_memory": topic_memory,
        }

    async def _extract_profile(self, text: str) -> list[dict]:
        """调用 LLM 提取用户画像增量。"""
        return await self._call_extraction_llm(
            system_prompt="""你是一个用户画像分析器。从对话记录中提取用户的长期偏好。

请分析以下维度：
1. 语言与格式偏好（输出语言、代码注释语言、输出格式等）
2. 交互风格偏好（详细程度、确认频率等）
3. 工具与技能偏好（工具优先级、执行策略等）
4. 质量要求（验证要求、测试要求等）

规则：
- 只提取用户明确表达或强烈暗示的偏好
- 不要过度推断
- 如果没有新的偏好线索，返回空列表
- 每条偏好包含 category（类别）和 content（具体内容）

返回 JSON 格式：
{"preferences": [{"category": "语言与格式偏好", "content": "用户希望所有回复使用中文"}]}
""",
            user_prompt=f"请从以下对话记录中提取用户的长期偏好：\n\n{text}",
        )

    async def _extract_topic(
        self, text: str, task_summary: str, user_feedback: str
    ) -> dict | None:
        """调用 LLM 提取主题记忆。"""
        result = await self._call_extraction_llm(
            system_prompt="""你是一个任务经验分析师。从对话记录中提取可用于未来参考的任务执行经验。

请提取以下信息：
- title: 简短的标题（15 字以内）
- task: 用户的任务描述
- approach: GraphAgent 采用的方案
- lessons: 经验教训（什么做得好，什么可以改进）
- topic_slug: 主题标识（英文，用下划线连接，如 code_analysis）
- topic_title: 主题中文名称（如 "代码分析"）
- keywords: 3-5 个中英文关键词

规则：
- 如果本轮没有实质性任务（如纯闲聊），返回 null
- 提取内容要具体，避免泛泛而谈
- 关注方案选择和执行过程中的关键决策

返回 JSON 格式：
{"has_memory": true, "memory": {"title": "...", "task": "...", "approach": "...", "lessons": "...", "topic_slug": "...", "topic_title": "...", "keywords": [...]}}
若没有可提取的经验则返回：{"has_memory": false}
""",
            user_prompt=(
                f"任务摘要: {task_summary}\n"
                f"用户反馈: {user_feedback or '无特殊反馈'}\n\n"
                f"对话记录:\n{text}"
            ),
        )
        if result and result.get("has_memory"):
            return result.get("memory")
        return None

    async def _call_extraction_llm(
        self, system_prompt: str, user_prompt: str
    ) -> dict | list:
        """调用 LLM 执行记忆提取，解析 JSON 返回结果。

        使用低成本的 LLM 调用（约 500 input + 200 output tokens）。
        失败时返回空结果，不影响主流程。
        """
        try:
            from graph_agent.llm import LLMFactory
            from langchain_core.messages import SystemMessage, HumanMessage
            from graph_agent.session.persistence import sanitize_text

            provider = LLMFactory.create_from_env()
            llm = provider.get_chat_model()

            messages = [
                SystemMessage(content=sanitize_text(system_prompt)),
                HumanMessage(content=sanitize_text(user_prompt)),
            ]
            response = await asyncio.to_thread(llm.invoke, messages, {"run_name": "MemoryExtractor"})
            text = response.content if hasattr(response, 'content') else str(response)

            return _parse_json_response(text)
        except Exception as e:
            logger.warning(f"记忆提取 LLM 调用失败: {e}")
            return {}

    def _build_extraction_text(self, messages: list) -> str:
        """将消息列表转为 LLM 可读的纯文本。"""
        lines = []
        for m in messages:
            try:
                role = getattr(m, "role", "unknown")
                content = getattr(m, "content", str(m))
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in content
                    )
                lines.append(f"[{role}]: {str(content)[:2000]}")
            except Exception:
                lines.append(str(m)[:2000])
        return "\n".join(lines[-40:])  # 最多取最近 40 条消息，控制 token 消耗


def _parse_json_response(text: str) -> dict | list:
    """从 LLM 响应中提取 JSON（与 guard.py 中使用相同策略）。"""
    cleaned = text.strip()

    # 1) 直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2) markdown 代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', cleaned)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3) 按大括号深度提取最外层 JSON
    start = cleaned.find('{') if not cleaned.startswith('[') else cleaned.find('[')
    if start >= 0:
        open_ch, close_ch = ('{', '}') if cleaned[start] == '{' else ('[', ']')
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start:i + 1])
                        except json.JSONDecodeError:
                            break

    return {}
