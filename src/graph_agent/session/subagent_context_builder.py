"""Layer 4 上下文构建器 —— SubAgent 执行视图。

为每个 SubAgent 提供完成任务所需的最小上下文，包含：
1. 任务详细信息（描述、参数、期望产出）
2. 依赖的前置输入（已完成任务的执行结果）
3. 任务背景的关键描述（用户意图、整体目标、相关历史消息片段）
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from graph_agent.message import MessageBlock
from graph_agent.message.message_type import MessageType
from graph_agent.session.token_counter import estimate_tokens

if TYPE_CHECKING:
    from graph_agent.orchestration.state import SubTask, TaskPlan


def _extract_keywords(text: str, min_len: int = 2) -> set[str]:
    """从文本中提取关键词。

    混合策略：
    - 英文/数字：正则分词，过滤短词
    - 中文：字符级 bigram 滑动窗口
    """
    keywords: set[str] = set()
    tokens = re.findall(r'[a-zA-Z0-9_]{2,}', text)
    keywords.update(t.lower() for t in tokens)

    chinese_chars = re.findall(r'[一-鿿]', text)
    for i in range(len(chinese_chars) - 1):
        keywords.add(chinese_chars[i] + chinese_chars[i + 1])
    for ch in chinese_chars:
        keywords.add(ch)

    return {k for k in keywords if len(k) >= min_len}


def _format_dependency_inputs(task: SubTask, all_tasks: list[SubTask]) -> str:
    """格式化依赖的前置输入。"""
    if not task.dependencies:
        return "(无依赖输入)"

    parts: list[str] = []
    for dep_id in task.dependencies:
        dep_task = next((t for t in all_tasks if t.task_id == dep_id), None)
        if dep_task is None:
            parts.append(f"- [{dep_id}]: (任务不存在)")
            continue
        if dep_task.status != "completed":
            parts.append(f"- [{dep_id}]: (任务状态: {dep_task.status})")
            continue

        agent_name = dep_task.assigned_agent or "unknown"
        result_preview = dep_task.result[:2000] if dep_task.result else "(无结果)"
        parts.append(
            f"### 前置任务 [{dep_id}] (执行者: {agent_name})\n"
            f"**任务描述**: {dep_task.description[:300]}\n"
            f"**执行结果**:\n{result_preview}"
        )
    return "\n\n".join(parts)


def _extract_relevant_context(
    guard_context: list[MessageBlock],
    task: SubTask,
    max_tokens: int = 1000,
) -> str:
    """从战略视图中提取与当前任务相关的上下文片段。"""
    keywords = _extract_keywords(task.description)

    relevant_parts: list[str] = []
    token_budget = max_tokens
    for msg in guard_context:
        msg_type = MessageType(msg.message_type)
        if msg_type not in (MessageType.USER_INPUT, MessageType.FINAL_ANSWER):
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        if not content:
            continue
        overlap = sum(1 for kw in keywords if kw in content.lower())
        if overlap >= 2:
            snippet = content[:500]
            line = f"[{msg_type.value}]: {snippet}"
            relevant_parts.append(line)
            token_budget -= estimate_tokens(line)
            if token_budget <= 0:
                break

    return "\n---\n".join(relevant_parts) if relevant_parts else "(无特别相关上下文)"


def _extract_intent_summary(intent_analysis: MessageBlock | None) -> str:
    """从意图分析消息中提取一句话意图摘要。"""
    if intent_analysis is None:
        return "(未提供)"
    content = intent_analysis.content
    text = content if isinstance(content, str) else str(content)
    return text[:200]


class SubAgentContextBuilder:
    """Layer 4 上下文构建器 —— SubAgent 执行视图。

    为每个 SubAgent 构建最小执行上下文，包含任务详情、依赖输入和背景描述。
    采用逐层构建模式（build_for_layer）以支持多层 DAG：
    在每层执行前调用，确保前置层的依赖结果已就绪。
    """

    def __init__(self, max_context_tokens: int = 8000):
        self._max_context_tokens = max_context_tokens

    def build_for_task(
        self,
        task: SubTask,
        all_tasks: list[SubTask],
        overall_goal: str,
        intent_analysis: MessageBlock | None,
        guard_context: list[MessageBlock],
        expected_output_format: str = "",
    ) -> str:
        """为单个 SubAgent 构建最小执行上下文。

        Returns:
            格式化的上下文文本，作为 SubAgent 的 HumanMessage 注入。
        """
        parts: list[str] = []

        # 1. 任务背景
        parts.append("## 任务背景")
        intent_summary = _extract_intent_summary(intent_analysis)
        parts.append(f"**用户意图**: {intent_summary}")
        parts.append(f"**整体目标**: {overall_goal}")
        if expected_output_format:
            parts.append(f"**期望产出格式**: {expected_output_format}")

        # 2. 任务详细信息
        parts.append("\n## 当前任务")
        parts.append(f"**任务ID**: {task.task_id}")
        parts.append(f"**所需技能**: {task.required_skill}")
        parts.append(f"**任务描述**:\n{task.description}")

        # 3. 依赖的前置输入
        parts.append("\n## 前置任务输出")
        parts.append(_format_dependency_inputs(task, all_tasks))

        # 4. 相关上下文
        parts.append("\n## 相关上下文")
        parts.append(_extract_relevant_context(guard_context, task))

        return "\n".join(parts)

    def build_for_layer(
        self,
        layer_tasks: list[SubTask],
        all_tasks: list[SubTask],
        overall_goal: str,
        intent_analysis: MessageBlock | None,
        guard_context: list[MessageBlock],
        expected_output_format: str = "",
    ) -> dict[str, str]:
        """为一层 DAG 任务构建执行上下文。

        在每层执行前调用，确保当前层的依赖任务结果（来自前序层）已就绪。

        Returns:
            {task_id: context_text} 映射
        """
        contexts: dict[str, str] = {}
        for task in layer_tasks:
            contexts[task.task_id] = self.build_for_task(
                task=task,
                all_tasks=all_tasks,
                overall_goal=overall_goal,
                intent_analysis=intent_analysis,
                guard_context=guard_context,
                expected_output_format=expected_output_format,
            )
        return contexts
