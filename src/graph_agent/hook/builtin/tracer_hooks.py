"""从 LLMCallbackHandler 迁移的终端输出 Hook（Type 3 — 纯观测）。

替代 tracer/llm_callback.py 的功能，通过 Hook 框架在工具/LLM 调用前后
自动触发终端格式化输出。

日志级别控制通过环境变量 GRAPHAGENT_LOG_LEVEL：
  - off:      不输出
  - phases:   仅阶段切换（由 OrchestrationTracer 处理）
  - llm_io:   阶段 + LLM 输入/输出（默认）
  - full:     阶段 + LLM I/O + 工具调用详情
"""

import os
import sys

from graph_agent.hook.base import hook, HookContext, HookType
from graph_agent.tracer.format import (
    print_llm_request, print_llm_response,
    print_tool_call, print_tool_result,
)

_LOG_LEVEL = os.getenv("GRAPHAGENT_LOG_LEVEL", "llm_io").lower()


def _show_llm() -> bool:
    return _LOG_LEVEL in ("llm_io", "full")


def _show_tools() -> bool:
    return _LOG_LEVEL == "full"


def _extract_system_user(messages: list) -> tuple[str, str]:
    """从 LangChain 消息列表中提取 system prompt 和 user text。"""
    system_text = ""
    user_text = ""
    for msg in (messages or []):
        role = getattr(msg, "type", "")
        content = getattr(msg, "content", str(msg))
        if role == "system":
            system_text = content
        elif role == "human":
            user_text = content
    return system_text, user_text


def _format_token_info(token_usage: dict | None) -> str:
    if not token_usage:
        return ""
    total = token_usage.get("total_tokens", 0)
    prompt_t = token_usage.get("prompt_tokens", token_usage.get("input_tokens", 0))
    completion_t = token_usage.get("completion_tokens", token_usage.get("output_tokens", 0))
    if total:
        return f"总 {total} tokens (入 {prompt_t} · 出 {completion_t})"
    if prompt_t or completion_t:
        return f"入 {prompt_t} · 出 {completion_t} tokens"
    return ""


@hook("before_llm_call", priority=500, hook_type=HookType.OBSERVE,
      description="终端输出 LLM 请求内容（System Prompt + User Message）")
def tracer_before_llm(ctx: HookContext) -> None:
    if not _show_llm():
        return
    caller = ctx.llm_caller or "LLM"
    system_text, user_text = _extract_system_user(ctx.llm_messages or [])
    print_llm_request(caller, system_text, user_text)


@hook("after_llm_call", priority=500, hook_type=HookType.OBSERVE,
      description="终端输出 LLM 响应内容和 Token 用量")
def tracer_after_llm(ctx: HookContext) -> None:
    if not _show_llm():
        return
    caller = ctx.llm_caller or "LLM"
    token_info = _format_token_info(ctx.llm_token_usage)
    print_llm_response(caller, ctx.llm_response or "", token_info)


@hook("before_tool_call", priority=500, hook_type=HookType.OBSERVE,
      description="终端输出工具调用参数（仅 full 级别）")
def tracer_before_tool(ctx: HookContext) -> None:
    if not _show_tools():
        return
    print_tool_call(ctx.tool_name or "unknown", ctx.tool_args or {})


@hook("after_tool_call", priority=500, hook_type=HookType.OBSERVE,
      description="终端输出工具调用结果（仅 full 级别）")
def tracer_after_tool(ctx: HookContext) -> None:
    if not _show_tools():
        return
    print_tool_result(ctx.tool_name or "unknown", str(ctx.tool_result or "")[:500])
