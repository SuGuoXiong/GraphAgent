from enum import Enum


class MessageType(Enum):
    # === 用户侧 ===
    USER_INPUT = "user_input"           # 用户输入
    USER_INTERRUPT = "user_interrupt"   # 用户中断当前执行
    USER_FEEDBACK = "user_feedback"     # 用户对 Agent 输出的反馈

    # === Agent 侧 ===
    AGENT_RESPONSE = "agent_response"       # Agent 最终文本回复
    AGENT_THINK = "agent_think"             # Agent 内部推理 / 思维链
    AGENT_ACTION = "agent_action"           # Agent 决定调用工具
    AGENT_PLAN = "agent_plan"               # Agent 生成的执行计划
    AGENT_SUMMARY = "agent_summary"         # Agent 对上下文的摘要压缩
    AGENT_REFLECTION = "agent_reflection"   # Agent 自反思 / 自评估
    AGENT_ERROR = "agent_error"             # Agent 自身错误

    # === 系统侧 ===
    SYSTEM_PROMPT = "system_prompt"             # 系统提示词
    SYSTEM_NOTIFICATION = "system_notification"  # 系统通知（超时、限额等）
    SYSTEM_STATE = "system_state"               # 状态变更通知

    # === 工具侧 ===
    TOOL_RESULT = "tool_result"  # 工具执行结果
