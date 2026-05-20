"""GraphAgent 可观测性模块。

提供 OrchestrationTracer 进行阶段追踪和决策记录。
LLM 调用和工具调用的终端输出已迁移到 Hook 机制：
  src/graph_agent/hook/builtin/tracer_hooks.py
"""

from graph_agent.tracer.tracer import OrchestrationTracer, get_tracer
from graph_agent.tracer.format import (
    print_phase_header, print_phase_end,
    print_llm_request, print_llm_response,
    print_decision, print_tool_call, print_tool_result, print_error,
)

__all__ = [
    "OrchestrationTracer",
    "get_tracer",
    "print_phase_header",
    "print_phase_end",
    "print_llm_request",
    "print_llm_response",
    "print_decision",
    "print_tool_call",
    "print_tool_result",
    "print_error",
]
