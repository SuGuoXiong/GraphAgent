"""GraphAgent 三层编排模块。

提供 GuardAgent / PlanAgent / SubAgent 三层编排架构，
实现意图识别 → 任务分解 → 方案审核 → 并行执行 → 结果汇总的完整流程。

使用示例:
    from graph_agent.orchestration import build_orchestration_graph
    graph = build_orchestration_graph()
    result = graph.invoke({"messages": [HumanMessage(content="...")]})
"""

from graph_agent.orchestration.state import (
    OrchestrationState,
    OrchestrationPhase,
    TaskPlan,
    SubTask,
)
from graph_agent.orchestration.prompt_loader import PromptLoader
from graph_agent.orchestration.subagent import SubAgentConfig, SubAgentRegistry
from graph_agent.orchestration.skill import Skill, SkillRegistry
from graph_agent.orchestration.graph import build_orchestration_graph

__all__ = [
    "OrchestrationState",
    "OrchestrationPhase",
    "TaskPlan",
    "SubTask",
    "PromptLoader",
    "SubAgentConfig",
    "SubAgentRegistry",
    "Skill",
    "SkillRegistry",
    "build_orchestration_graph",
]
