"""编排流程专用状态定义，在 AgentState 基础上扩展。"""

from typing import Annotated, Any
from dataclasses import dataclass, field
from enum import Enum
import operator

from graph_agent.state import AgentState


class OrchestrationPhase(Enum):
    """编排流程所处阶段"""
    INTENT_ANALYSIS = "intent_analysis"       # GuardAgent 分析用户意图
    PLAN_GENERATION = "plan_generation"       # PlanAgent 制定方案
    PLAN_REVIEW = "plan_review"               # GuardAgent 审核方案
    TASK_EXECUTION = "task_execution"         # SubAgent 并行执行子任务
    RESULT_SYNTHESIS = "result_synthesis"     # PlanAgent 汇总结果
    RESULT_REVIEW = "result_review"           # GuardAgent 审核最终结果
    COMPLETED = "completed"                   # 任务完成


@dataclass
class SubTask:
    """单个子任务定义"""
    task_id: str
    description: str
    required_skill: str
    assigned_agent: str = ""
    input_data: dict = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    status: str = "pending"
    result: str = ""
    error: str = ""


@dataclass
class TaskPlan:
    """PlanAgent 生成的任务计划"""
    plan_id: str
    overall_goal: str
    sub_tasks: list[SubTask]
    execution_strategy: str = "parallel"
    expected_output_format: str = ""
    created_at: str = ""


class OrchestrationState(AgentState):
    """编排状态，继承 AgentState 的双通道消息机制"""
    phase: OrchestrationPhase
    intent: str
    guard_feedback: str
    task_plan: TaskPlan | None
    sub_results: Annotated[dict[str, str], operator.or_]
    plan_approved: bool
    result_approved: bool
    review_retries: int
    max_review_retries: int
    final_answer: str
    _interrupt_event: Any = None
    _injected_messages: list | None = None
    _ask_user_llm_response: Any = None
    _subagent_messages: list | None = None
    _ask_user_tool_id: str = ""
