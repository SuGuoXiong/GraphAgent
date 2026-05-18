"""PlanAgent 节点——任务分解、方案制定、派发执行、结果汇总。

PlanAgent 不可见、不可调用任何 Tool，只能看到从配置文件加载的 SubAgent 清单。
"""

import json
import re
import uuid

from graph_agent.orchestration.state import (
    OrchestrationState, OrchestrationPhase, TaskPlan, SubTask,
)
from graph_agent.orchestration.prompt_loader import PromptLoader
from graph_agent.orchestration.subagent import SubAgentRegistry
from graph_agent.message import (
    create_assistant_message, create_system_message,
    generate_message_id,
    agent_message_to_langchain,
)
from graph_agent.message.message_type import MessageType
from graph_agent.tracer import get_tracer

_prompt_loader = PromptLoader()
_registry = SubAgentRegistry()


def _call_llm(system_prompt: str, user_text: str,
              state: OrchestrationState,
              name: str = "PlanAgent") -> str:
    """调用 LLM 并返回文本内容。"""
    from graph_agent.llm import LLMFactory

    provider = LLMFactory.create_from_env()
    llm = provider.get_chat_model()

    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_text)]
    response = llm.invoke(messages, config={"run_name": name})
    return response.content if hasattr(response, 'content') else str(response)


def _format_context(messages: list) -> str:
    """格式化对话上下文。"""
    if not messages:
        return "(无历史消息)"
    parts = []
    for m in messages[-10:]:
        content = m.content if isinstance(m.content, str) else str(m.content)
        parts.append(f"[{m.type if hasattr(m, 'type') else '?'}]: {content[:500]}")
    return "\n".join(parts)


def _format_sub_results(sub_results: dict) -> str:
    """格式化子任务结果。"""
    if not sub_results:
        return "(无已完成结果)"
    parts = [f"  {k}: {v[:300]}" for k, v in sub_results.items()]
    return "\n".join(parts)


def _parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON。"""
    import re

    cleaned = text.strip()

    # 1) 先尝试直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2) 从 markdown 代码块中提取 ```json ... ```
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', cleaned)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3) 按大括号深度提取最外层 JSON 对象
    start = cleaned.find('{')
    if start >= 0:
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
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        json_str = cleaned[start:i + 1]
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            break

    # 4) 回退：尝试简单正则
    match = re.search(r'\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {}


def _generate_plan(state: OrchestrationState) -> dict:
    """方案制定阶段：将用户意图分解为 TaskPlan。"""
    get_tracer().trace_phase("方案制定", "PlanAgent", "将用户意图分解为可执行的子任务计划")

    system_prompt = _prompt_loader.load_with_context(
        "plan", "plan_generation",
        intent=state.get("intent", ""),
        guard_feedback=state.get("guard_feedback", ""),
        available_subagents=_registry.describe_all_for_llm(),
        conversation_context=_format_context(state.get("messages", [])),
    )
    user_text = f"请为以下意图制定任务计划:\n{state.get('intent', '')}"
    text = _call_llm(system_prompt, user_text, state, name="PlanAgent")
    result = _parse_json_response(text)

    sub_tasks_raw = result.get("sub_tasks", [])
    sub_tasks = []
    for i, st in enumerate(sub_tasks_raw):
        sub_tasks.append(SubTask(
            task_id=st.get("task_id", f"task_{i+1}"),
            description=st.get("description", ""),
            required_skill=st.get("required_skill", ""),
            dependencies=st.get("dependencies", []),
            input_data=st.get("input_data", {}),
        ))

    task_plan = TaskPlan(
        plan_id=result.get("plan_id", str(uuid.uuid4().hex[:8])),
        overall_goal=result.get("overall_goal", state.get("intent", "")),
        sub_tasks=sub_tasks,
        execution_strategy=result.get("execution_strategy", "parallel"),
        expected_output_format=result.get("expected_output_format", ""),
    )

    msg = create_assistant_message(
        content=text,
        name="PlanAgent",
        message_type=MessageType.PLAN_PROPOSAL,
    )

    return {
        "phase": OrchestrationPhase.PLAN_REVIEW,
        "task_plan": task_plan,
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def _resolve_placeholders(text: str, sub_results: dict[str, str]) -> str:
    """将文本中的 {{task_id}} 和 {{task_id.result}} 占位符替换为实际执行结果。"""
    if not text or not sub_results:
        return text

    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        # {{task_id.result}} → 取 .result 后缀
        if key.endswith(".result"):
            task_id = key[:-7]
        else:
            task_id = key
        return sub_results.get(task_id, match.group(0))

    return re.sub(r'\{\{(.+?)\}\}', _replace, text)


def _dispatch_tasks(state: OrchestrationState) -> dict:
    """任务派发阶段：将就绪的子任务派发给匹配的 SubAgent。"""
    get_tracer().trace_phase("任务派发", "PlanAgent", "将就绪子任务派发给匹配的 SubAgent")

    task_plan = state.get("task_plan")
    if not task_plan:
        return {"phase": OrchestrationPhase.COMPLETED}

    sub_tasks = task_plan.sub_tasks
    sub_results = state.get("sub_results", {})
    dispatch_messages = []

    for task in sub_tasks:
        if task.status == "pending":
            deps_met = all(
                sub_results.get(dep_id) is not None
                for dep_id in task.dependencies
            )
            if deps_met:
                # 用已完成的子任务结果填充占位符
                task.description = _resolve_placeholders(task.description, sub_results)
                resolved_input_data = {
                    k: _resolve_placeholders(v, sub_results)
                    for k, v in task.input_data.items()
                }
                task.input_data = resolved_input_data

                # 将解析后的 input_data 追加到任务描述中，确保 SubAgent 获得完整上下文
                if resolved_input_data:
                    param_lines = []
                    for k, v in resolved_input_data.items():
                        if "\n" in v:
                            param_lines.append(f"- {k}:\n  \"\"\"\n  {v}\n  \"\"\"")
                        else:
                            param_lines.append(f"- {k}: {v}")
                    params_block = "\n".join(param_lines)
                    task.description = f"{task.description}\n\n【执行参数】\n{params_block}"

                candidates = _registry.find_by_skill(task.required_skill)
                # 精确匹配失败时回退到 general_agent
                if not candidates and task.required_skill:
                    candidates = _registry.find_by_skill("text_generation")
                    if not candidates:
                        try:
                            candidates = [_registry.get("general_agent")]
                        except KeyError:
                            pass
                    if candidates:
                        get_tracer().trace_decision(
                            "PlanAgent",
                            f"技能 '{task.required_skill}' 无精确匹配，回退到 {candidates[0].name}",
                        )
                if candidates:
                    task.status = "running"
                    task.assigned_agent = candidates[0].name
                    msg = create_assistant_message(
                        content=task.description,
                        name="PlanAgent",
                        message_type=MessageType.PLAN_TASK_DISPATCH,
                        metadata={
                            "task_id": task.task_id,
                            "required_skill": task.required_skill,
                            "assigned_agent": task.assigned_agent,
                        },
                    )
                    dispatch_messages.append(msg)
                else:
                    task.status = "failed"
                    task.error = f"未找到匹配技能 '{task.required_skill}' 的 SubAgent"

    all_done = all(t.status in ("completed", "failed") for t in sub_tasks)
    if not dispatch_messages and not all_done:
        return {}

    next_phase = OrchestrationPhase.RESULT_SYNTHESIS if all_done else OrchestrationPhase.TASK_EXECUTION

    return {
        "phase": next_phase,
        "task_plan": task_plan,
        "ga_messages": dispatch_messages if dispatch_messages else [],
        "messages": [agent_message_to_langchain(m) for m in dispatch_messages] if dispatch_messages else [],
    }


def _synthesize_results(state: OrchestrationState) -> dict:
    """结果汇总阶段：整合所有 SubAgent 的执行结果。"""
    get_tracer().trace_phase("结果汇总", "PlanAgent", "整合所有 SubAgent 的执行结果")

    task_plan = state.get("task_plan")
    sub_results = state.get("sub_results", {})

    system_prompt = _prompt_loader.load_with_context(
        "plan", "result_synthesis",
        intent=state.get("intent", ""),
        guard_feedback=state.get("guard_feedback", ""),
        task_plan=str(task_plan),
        sub_results=_format_sub_results(sub_results),
        conversation_context=_format_context(state.get("messages", [])),
    )
    user_text = f"请汇总以下子任务结果:\n{_format_sub_results(sub_results)}"
    text = _call_llm(system_prompt, user_text, state, name="PlanAgent")

    # 从 LLM 返回的 JSON 中提取最终的文本摘要
    result_json = _parse_json_response(text)
    final_answer = result_json.get("summary", text)

    msg = create_assistant_message(
        content=text,
        name="PlanAgent",
        message_type=MessageType.PLAN_RESULT_SYNTHESIS,
    )

    return {
        "phase": OrchestrationPhase.RESULT_REVIEW,
        "final_answer": final_answer,
        "ga_messages": [msg],
        "messages": [agent_message_to_langchain(msg)],
    }


def plan_node(state: OrchestrationState) -> dict:
    """PlanAgent 节点：根据当前 phase 执行对应职责。"""
    phase = state.get("phase", OrchestrationPhase.PLAN_GENERATION)

    if phase == OrchestrationPhase.PLAN_GENERATION:
        return _generate_plan(state)
    elif phase == OrchestrationPhase.TASK_EXECUTION:
        return _dispatch_tasks(state)
    elif phase == OrchestrationPhase.RESULT_SYNTHESIS:
        return _synthesize_results(state)
    return {}


def plan_router(state: OrchestrationState) -> str:
    """PlanAgent 的路由决策。

    注意：此时 state 已包含 plan_node 的返回值，
    因此 phase 反映的是 plan_node 执行*后*的状态。
    """
    phase = state.get("phase", OrchestrationPhase.PLAN_GENERATION)

    if phase == OrchestrationPhase.PLAN_REVIEW:
        # plan 刚完成方案制定 → GuardAgent 审核方案
        return "guard"

    if phase == OrchestrationPhase.TASK_EXECUTION:
        # 有 running 任务 → SubAgent 执行，否则 → PlanAgent 重新调度
        task_plan = state.get("task_plan")
        if task_plan and any(t.status == "running" for t in task_plan.sub_tasks):
            return "sub_exec"
        return "plan"

    if phase == OrchestrationPhase.RESULT_SYNTHESIS:
        # 所有子任务完成 → plan 再次执行（汇总结果）
        return "plan"

    if phase == OrchestrationPhase.RESULT_REVIEW:
        # plan 刚完成结果汇总 → GuardAgent 验收结果
        return "guard"

    if phase == OrchestrationPhase.COMPLETED:
        return "__end__"

    return "__end__"
