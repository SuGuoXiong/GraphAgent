"""按三层编排架构分层的消息类型定义。

类型前缀指示消息所属层级和压缩优先级：

    P0 (绝不压缩):  USER_*
    P1 (不可丢弃):  SYSTEM_*, GUARD_*
    P2 (可摘要):    PLAN_*
    P3 (可压缩):    SUBAGENT_*
    P4 (高度可压):  TOOL_*
"""

from enum import Enum


class MessageType(Enum):
    # === P0: 用户侧 ===
    USER_INPUT = "user_input"
    USER_INTERRUPT = "user_interrupt"
    USER_FEEDBACK = "user_feedback"

    # === P1: 系统侧 ===
    SYSTEM_PROMPT = "system_prompt"
    SYSTEM_NOTIFICATION = "system_notification"

    # === P1: GuardAgent 层 —— 质量把关，决策性消息 ===
    GUARD_INTENT_ANALYSIS = "guard_intent_analysis"  # 意图分析结果
    GUARD_PLAN_REVIEW = "guard_plan_review"           # 方案审核（approved / rejected）
    FINAL_ANSWER = "final_answer"                     # Agent 呈现给用户的最终回复

    # === P2: PlanAgent 层 —— 任务规划，结构性消息 ===
    PLAN_PROPOSAL = "plan_proposal"                   # 方案制定（TaskPlan JSON）
    PLAN_TASK_DISPATCH = "plan_task_dispatch"         # 任务派发指令
    PLAN_RESULT_SYNTHESIS = "plan_result_synthesis"   # 结果汇总（含 final_answer）

    # === P3: SubAgent 层 —— 子任务执行 ===
    SUBAGENT_TASK_RESULT = "subagent_task_result"     # 任务执行结果

    # === P4: 工具侧 —— 原子操作 ===
    TOOL_CALL = "tool_call"                           # Agent 调用工具请求
    TOOL_RESULT = "tool_result"                       # 工具执行结果

    # === 通用回退（仅用于 LangChain 反向转换等无上下文场景）===
    AGENT_RESPONSE = "agent_response"


# 压缩优先级 P0-P4，数值越小越重要，越不应被压缩
_COMPRESSION_PRIORITY: dict[MessageType, int] = {
    MessageType.USER_INPUT: 0,
    MessageType.USER_INTERRUPT: 0,
    MessageType.USER_FEEDBACK: 0,
    MessageType.SYSTEM_PROMPT: 1,
    MessageType.SYSTEM_NOTIFICATION: 1,
    MessageType.GUARD_INTENT_ANALYSIS: 1,
    MessageType.GUARD_PLAN_REVIEW: 1,
    MessageType.FINAL_ANSWER: 1,
    MessageType.PLAN_PROPOSAL: 2,
    MessageType.PLAN_TASK_DISPATCH: 2,
    MessageType.PLAN_RESULT_SYNTHESIS: 2,
    MessageType.SUBAGENT_TASK_RESULT: 3,
    MessageType.TOOL_CALL: 4,
    MessageType.TOOL_RESULT: 4,
    MessageType.AGENT_RESPONSE: 2,
}


def get_compression_priority(message_type: MessageType) -> int:
    """返回消息类型的压缩优先级（0-4，值越小越重要，越不应被压缩）。"""
    return _COMPRESSION_PRIORITY.get(message_type, 2)
