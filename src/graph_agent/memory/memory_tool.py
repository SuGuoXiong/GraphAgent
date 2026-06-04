"""search_memory 工具注册 —— 将记忆检索注册为 Agent 可调用工具。

工具 schema（供 LLM 理解）:
    name: search_memory
    description: 检索历史记忆
    parameters: query (required), type, scope, include_archived, limit
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from graph_agent.tools.base import tool

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _get_memory_manager():
    """延迟获取 MemoryManager 单例。"""
    from graph_agent.orchestration.context_utils import get_memory_manager
    return get_memory_manager()


@tool(
    name="search_memory",
    description=(
        "检索历史记忆。根据 query（关键字）匹配已存储的记忆标签和内容，"
        "可选择性按 type（记忆类型）和 scope（作用范围）过滤。"
        "返回最相关的前 N 条活跃记忆。"
        "适用场景：用户提到某个偏好或规范时检索相关记忆；"
        "执行复杂任务前检索过往经验和避坑档案；"
        "需要回忆项目架构决策时检索 arch_design 类记忆。"
    ),
    risk_level="low",
)
def search_memory(
    query: str,
    type: str | None = None,
    scope: str | None = None,
    include_archived: bool = False,
    limit: int = 3,
) -> str:
    """检索历史记忆。

    Args:
        query: 检索关键字，用于匹配记忆的 tags 标签和 content 正文。
               使用 FTS5 全文检索，支持中英文混合关键词。
               建议从当前任务/问题中提取 1~5 个核心关键词。
               例如：'Python 缩进 代码风格'、'用户偏好 回复语言'
        type: 记忆类型过滤（可选）。
              user_preference=用户偏好, arch_design=架构设计,
              project_rule=项目规范, design_pattern=编码建模,
              bug_pitfall=Bug踩坑, general_knowledge=通用常识
        scope: 作用范围过滤（可选）。
               user=用户级, project=项目级, session=会话级
        include_archived: 是否包含归档记忆（可选，默认 false）。
                          设为 true 时同时检索 ACTIVE 和 ARCHIVED 状态的记忆
        limit: 返回条数上限，范围 1-3，默认 3

    Returns:
        JSON 格式的检索结果列表，无结果时返回空数组 "[]"
    """
    manager = _get_memory_manager()
    if manager is None:
        return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)

    from graph_agent.memory.memory_types import SearchMemoryInput

    input_ = SearchMemoryInput(
        query=query,
        type=type,
        scope=scope,
        include_archived=include_archived,
        limit=min(max(limit, 1), 3),
    )

    results = manager.search(
        query=input_.query,
        type=input_.type,
        scope=input_.scope,
        include_archived=input_.include_archived,
        limit=input_.limit,
    )

    if not results:
        return "[]"

    output = []
    for r in results:
        output.append({
            "id": r.id,
            "type": r.type,
            "scope": r.scope,
            "status": r.status,
            "tags": r.tags,
            "content": r.content,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        })

    return json.dumps(output, ensure_ascii=False, indent=2)


# 注册到工具中心的入口函数
def register_memory_tool() -> None:
    """将 search_memory 工具注册到全局 ToolCenter。

    在 MemoryManager 初始化时调用。
    """
    try:
        from graph_agent.tools.base import ToolCenter

        # 创建临时 ToolCenter 实例来触发 @tool 装饰器的注册
        # 注意：@tool 装饰器返回 AgentTool 实例，需要手动注册
        center = ToolCenter()
        # 检查是否已经注册
        existing = None
        try:
            existing = center.get_tool("search_memory")
        except KeyError:
            pass

        if existing is None:
            center.register(search_memory)  # type: ignore[arg-type]
            logger.info("search_memory 工具已注册")
    except ImportError:
        logger.debug("ToolCenter 不可用，search_memory 工具将在可用时注册")
