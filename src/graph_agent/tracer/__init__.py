"""GraphAgent 可观测性模块。

提供 OrchestrationTracer 和 LLMCallbackHandler，
实现阶段追踪、LLM 输入/输出记录、工具调用记录。
"""

from graph_agent.tracer.tracer import OrchestrationTracer, get_tracer
from graph_agent.tracer.llm_callback import LLMCallbackHandler
from graph_agent.tracer.format import (
    print_phase_header, print_phase_end,
    print_llm_request, print_llm_response,
    print_decision, print_tool_call, print_tool_result, print_error,
)

__all__ = [
    "OrchestrationTracer",
    "get_tracer",
    "LLMCallbackHandler",
    "print_phase_header",
    "print_phase_end",
    "print_llm_request",
    "print_llm_response",
    "print_decision",
    "print_tool_call",
    "print_tool_result",
    "print_error",
]
