"""检查点机制 —— 协作式中断、状态序列化与恢复。

提供编排流程的暂停/恢复能力：
- InterruptException: 携带当前编排状态的异常，在安全边界抛出
- _check_interrupt: 节点内调用，检测中断信号
- serialize_checkpoint / deserialize_checkpoint: 状态持久化
- generate_recovery_hint: 生成用户可读的恢复提示
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class InterruptException(Exception):
    """编排图节点在安全边界检测到中断信号时抛出此异常。

    携带当前完整的编排状态作为检查点数据，
    由 ACPServer.execute_turn() 捕获并持久化。
    """

    def __init__(self, state: dict):
        super().__init__("编排流程已中断")
        self.state = state


class AskUserException(Exception):
    """Agent 向用户提问时抛出的异常。

    由 before_tool_call Hook 拦截 ask_user 工具调用时抛出，
    被 ACPServer.execute_turn() 捕获并转换为 ASK_USER 事件。
    """

    def __init__(self, question: str, options: list[str] | None = None,
                 require_approval: bool = False, state: dict | None = None):
        self.question = question
        self.options = options
        self.require_approval = require_approval
        self.state = state or {}
        super().__init__(question)


def _check_interrupt(state: dict) -> None:
    """编排图节点在完成核心逻辑后调用此函数检查中断信号。

    从 state 中读取 _interrupt_event (asyncio.Event)，
    若已设置则抛出 InterruptException 以中断执行流程。

    在无事件循环或 _interrupt_event 未注入时静默跳过，
    保证该函数在非 ACP 场景（如 debug.py 直接调用）下不产生副作用。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    interrupt_event = state.get("_interrupt_event")
    if interrupt_event is not None and interrupt_event.is_set():
        raise InterruptException(dict(state))


def serialize_checkpoint(state: dict, session_id: str, reason: str) -> dict:
    """将 OrchestrationState 序列化为可持久化的检查点字典。

    处理关键字段：
    - phase, intent, plan_approved, result_approved 等基础字段
    - task_plan (dataclass → dict，含 SubTask 列表)
    - messages (LangChain 消息 → dict)
    - ga_messages (MessageBlock → 兼容 dict)
    - sub_results (dict[str, str])

    不序列化 _interrupt_event (asyncio.Event 不可持久化)。
    """
    from langchain_core.messages import message_to_dict

    # ── task_plan 序列化 ──────────────────────────────
    task_plan = state.get("task_plan")
    task_plan_dict = None
    if task_plan is not None:
        task_plan_dict = {
            "plan_id": task_plan.plan_id,
            "overall_goal": task_plan.overall_goal,
            "sub_tasks": [
                {
                    "task_id": t.task_id,
                    "description": t.description,
                    "required_skill": t.required_skill,
                    "assigned_agent": t.assigned_agent,
                    "input_data": t.input_data,
                    "dependencies": t.dependencies,
                    "status": t.status,
                    "result": t.result,
                    "error": t.error,
                }
                for t in task_plan.sub_tasks
            ],
            "execution_strategy": task_plan.execution_strategy,
            "expected_output_format": task_plan.expected_output_format,
        }

    # ── messages 序列化 ───────────────────────────────
    messages = state.get("messages", [])
    serialized_messages = [message_to_dict(m) for m in messages]

    # ── ga_messages 序列化 ────────────────────────────
    from graph_agent.session.persistence import _serialize_message
    ga_msgs = state.get("ga_messages", [])
    serialized_ga = []
    for m in ga_msgs:
        try:
            serialized_ga.append(_serialize_message(m))
        except Exception:
            serialized_ga.append(str(m))

    # ── _ask_user_llm_response 序列化 ─────────────────
    ask_user_llm_resp = state.get("_ask_user_llm_response")
    serialized_ask_user_llm = None
    if ask_user_llm_resp is not None:
        try:
            serialized_ask_user_llm = message_to_dict(ask_user_llm_resp)
        except Exception:
            serialized_ask_user_llm = None

    # ── _ask_user_tool_id 序列化 ──────────────────────
    ask_user_tool_id = state.get("_ask_user_tool_id", "")

    # ── phase 序列化 ──────────────────────────────────
    phase_val = state.get("phase", "")
    if hasattr(phase_val, "value"):
        phase_str = phase_val.value
    else:
        phase_str = str(phase_val) if phase_val else ""

    checkpoint = {
        "phase": phase_str,
        "intent": state.get("intent", ""),
        "task_plan": task_plan_dict,
        "sub_results": state.get("sub_results", {}),
        "plan_approved": state.get("plan_approved", False),
        "result_approved": state.get("result_approved", False),
        "review_retries": state.get("review_retries", 0),
        "final_answer": state.get("final_answer", ""),
        "messages": serialized_messages,
        "ga_messages": serialized_ga,
        "_ask_user_llm_response": serialized_ask_user_llm,
        "_ask_user_tool_id": ask_user_tool_id,
        "session_id": session_id,
        "created_at": _iso_now(),
        "reason": reason,
    }

    checkpoint["recovery_hint"] = generate_recovery_hint(checkpoint)
    return checkpoint


def deserialize_checkpoint(checkpoint: dict) -> dict:
    """从检查点字典恢复 OrchestrationState 的初始值。

    恢复内容包括：
    - 基础状态字段 (phase, intent, plan_approved, result_approved 等)
    - task_plan (dict → TaskPlan dataclass)
    - messages (dict → LangChain 消息)
    - ga_messages (dict → MessageBlock)
    - sub_results
    """
    from langchain_core.messages import messages_from_dict
    from graph_agent.orchestration.state import (
        OrchestrationPhase, TaskPlan, SubTask,
    )
    from graph_agent.session.persistence import _deserialize_message

    # ── phase 恢复 ────────────────────────────────────
    phase_str = checkpoint.get("phase", "")
    try:
        phase = OrchestrationPhase(phase_str)
    except ValueError:
        phase = OrchestrationPhase.INTENT_ANALYSIS

    # ── task_plan 恢复 ────────────────────────────────
    task_plan = None
    tp_dict = checkpoint.get("task_plan")
    if tp_dict:
        sub_tasks = []
        for st in tp_dict.get("sub_tasks", []):
            sub_tasks.append(SubTask(
                task_id=st.get("task_id", ""),
                description=st.get("description", ""),
                required_skill=st.get("required_skill", ""),
                assigned_agent=st.get("assigned_agent", ""),
                input_data=st.get("input_data", {}),
                dependencies=st.get("dependencies", []),
                status=st.get("status", "pending"),
                result=st.get("result", ""),
                error=st.get("error", ""),
            ))
        task_plan = TaskPlan(
            plan_id=tp_dict.get("plan_id", ""),
            overall_goal=tp_dict.get("overall_goal", ""),
            sub_tasks=sub_tasks,
            execution_strategy=tp_dict.get("execution_strategy", "parallel"),
            expected_output_format=tp_dict.get("expected_output_format", ""),
        )

    # ── messages 恢复 ─────────────────────────────────
    serialized_messages = checkpoint.get("messages", [])
    messages = messages_from_dict(serialized_messages)

    # ── ga_messages 恢复 ──────────────────────────────
    ga_msgs = []
    for raw in checkpoint.get("ga_messages", []):
        if isinstance(raw, dict) and "role" in raw:
            try:
                ga_msgs.append(_deserialize_message(raw))
            except Exception:
                pass

    # ── _ask_user_llm_response 恢复 ───────────────────
    ask_user_llm_raw = checkpoint.get("_ask_user_llm_response")
    ask_user_llm_resp = None
    if ask_user_llm_raw:
        try:
            msgs = messages_from_dict([ask_user_llm_raw])
            ask_user_llm_resp = msgs[0] if msgs else None
        except Exception:
            ask_user_llm_resp = None

    return {
        "phase": phase,
        "intent": checkpoint.get("intent", ""),
        "task_plan": task_plan,
        "sub_results": checkpoint.get("sub_results", {}),
        "plan_approved": checkpoint.get("plan_approved", False),
        "result_approved": checkpoint.get("result_approved", False),
        "review_retries": checkpoint.get("review_retries", 0),
        "final_answer": checkpoint.get("final_answer", ""),
        "messages": messages,
        "ga_messages": ga_msgs,
        "_ask_user_llm_response": ask_user_llm_resp,
        "_ask_user_tool_id": checkpoint.get("_ask_user_tool_id", ""),
    }


def generate_recovery_hint(checkpoint: dict) -> str:
    """根据检查点状态生成用户可读的中文恢复提示。"""
    phase = checkpoint.get("phase", "")
    plan = checkpoint.get("task_plan")
    sub_results = checkpoint.get("sub_results", {})

    if not plan:
        return "任务尚未制定计划"

    total_tasks = len(plan.get("sub_tasks", []))
    completed = len(sub_results)

    hints = {
        "intent_analysis": "正在分析您的意图，尚未开始执行",
        "plan_generation": "正在制定任务计划",
        "plan_review": "任务计划已制定，等待审核",
        "task_execution": f"任务已执行 {completed}/{total_tasks} 个子任务，恢复后将继续执行剩余子任务",
        "result_synthesis": "子任务已全部执行完毕，正在汇总结果",
        "result_review": "结果汇总已完成，等待审核",
        "completed": "任务已全部完成",
    }
    return hints.get(phase, f"编排阶段: {phase}")
