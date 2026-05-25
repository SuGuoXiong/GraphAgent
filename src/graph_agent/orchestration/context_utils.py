"""上下文构建工具函数 —— 供编排节点共用。

提供 Builder 单例获取和 ConversationHistory 查找能力。
"""

from __future__ import annotations

from graph_agent.session.compressor import SessionConfig
from graph_agent.session.guard_context_builder import GuardContextBuilder
from graph_agent.session.plan_context_builder import PlanContextBuilder
from graph_agent.session.subagent_context_builder import SubAgentContextBuilder
from graph_agent.session.history import ConversationHistory


# 模块级 Builder 单例缓存
_guard_context_builder: GuardContextBuilder | None = None
_plan_context_builder: PlanContextBuilder | None = None
_subagent_context_builder: SubAgentContextBuilder | None = None


def _get_session_config() -> SessionConfig:
    return SessionConfig.from_yaml()


def get_guard_context_builder() -> GuardContextBuilder:
    global _guard_context_builder
    if _guard_context_builder is None:
        _guard_context_builder = GuardContextBuilder(_get_session_config())
    return _guard_context_builder


def get_plan_context_builder() -> PlanContextBuilder:
    global _plan_context_builder
    if _plan_context_builder is None:
        _plan_context_builder = PlanContextBuilder(
            _get_session_config(),
            guard_context_builder=get_guard_context_builder(),
        )
    return _plan_context_builder


def get_subagent_context_builder() -> SubAgentContextBuilder:
    global _subagent_context_builder
    if _subagent_context_builder is None:
        _subagent_context_builder = SubAgentContextBuilder()
    return _subagent_context_builder


def get_history_from_state(state: dict) -> ConversationHistory | None:
    """从 state 中获取当前会话的 ConversationHistory。

    优先通过 _session_id 从 SessionManager 获取（ACP 场景）；
    回退到 None（非 ACP 场景，调用方自行处理）。
    """
    session_id = state.get("_session_id", "")
    if not session_id:
        return None

    try:
        from graph_agent.acp.session_manager import _session_manager
        ctx = _session_manager.get_context(session_id)
        return ctx.history if ctx else None
    except Exception:
        return None
