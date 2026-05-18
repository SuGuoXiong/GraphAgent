"""三层编排图——组装 GuardAgent / PlanAgent / SubAgent 的执行流程。"""

from langgraph.graph import StateGraph, START, END

from graph_agent.orchestration.state import OrchestrationState, OrchestrationPhase


def _init_state(state: OrchestrationState) -> dict:
    """初始化编排状态。"""
    return {
        "phase": OrchestrationPhase.INTENT_ANALYSIS,
        "intent": "",
        "guard_feedback": "",
        "task_plan": None,
        "sub_results": {},
        "plan_approved": False,
        "result_approved": False,
        "review_retries": 0,
        "max_review_retries": 3,
    }


def build_orchestration_graph() -> StateGraph:
    """构建三层编排图。

    Returns:
        编译后的 StateGraph 实例
    """
    # Lazy imports to avoid circular dependencies:
    # orchestration/__init__.py → graph.py → node/guard.py → orchestration/state.py
    from graph_agent.node.guard import guard_node, guard_router
    from graph_agent.node.plan import plan_node, plan_router
    from graph_agent.node.subagent import subagent_exec_node

    builder = StateGraph(OrchestrationState)

    builder.add_node("guard", guard_node)
    builder.add_node("plan", plan_node)
    builder.add_node("sub_exec", subagent_exec_node)

    builder.add_edge(START, "guard")

    builder.add_conditional_edges("guard", guard_router, {
        "plan": "plan",
        "__end__": END,
    })

    builder.add_conditional_edges("plan", plan_router, {
        "guard": "guard",
        "sub_exec": "sub_exec",
        "plan": "plan",
        "__end__": END,
    })

    builder.add_edge("sub_exec", "plan")

    return builder.compile()
