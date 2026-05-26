"""上下文构建工具函数 —— 供编排节点共用。

提供 Builder 单例获取、ConversationHistory 查找和记忆系统集成。
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

# 记忆系统单例
_memory_manager = None
_preference_store = None
_agent_memory_store = None


def _get_session_config() -> SessionConfig:
    return SessionConfig.from_yaml()


def _init_memory_system():
    """延迟初始化记忆系统（首次调用时触发）。"""
    global _memory_manager, _preference_store, _agent_memory_store
    if _memory_manager is None:
        from graph_agent.memory import MemoryManager, UserPreferenceStore, AgentMemoryStore
        _memory_manager = MemoryManager()
        _preference_store = UserPreferenceStore(_memory_manager)
        _agent_memory_store = AgentMemoryStore(_memory_manager)


def get_memory_manager() -> "MemoryManager | None":
    """获取记忆系统统一入口（单例）。"""
    _init_memory_system()
    return _memory_manager


def get_guard_context_builder() -> GuardContextBuilder:
    global _guard_context_builder
    if _guard_context_builder is None:
        _init_memory_system()
        _guard_context_builder = GuardContextBuilder(
            _get_session_config(),
            preference_store=_preference_store,
        )
    return _guard_context_builder


def get_plan_context_builder() -> PlanContextBuilder:
    global _plan_context_builder
    if _plan_context_builder is None:
        _init_memory_system()
        _plan_context_builder = PlanContextBuilder(
            _get_session_config(),
            guard_context_builder=get_guard_context_builder(),
            memory_store=_agent_memory_store,
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
