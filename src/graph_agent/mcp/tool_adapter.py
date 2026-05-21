"""MCP 工具适配——将 MCP Tool 包装为 AgentTool，提供同步/异步桥接的运行时调用。

MCP session 存活于后台事件循环线程中，所有 call_tool 通过
run_coroutine_threadsafe 提交到该循环执行。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any


class _SessionContext:
    """封装单个 MCP Server 的 session 和 transport 上下文。

    对于 stdio transport，持有 transport 上下文管理器以维持子进程生命周期。
    对于 streamable-http transport，持有 client 上下文以维持连接池。
    """

    def __init__(self):
        self.session: Any = None           # ClientSession 实例
        self._transport_ctx: Any = None    # stdio_client / streamablehttp_client 上下文
        self._session_ctx: Any = None      # ClientSession 上下文（供 cleanup 用）
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread_id: int | None = None


# 全局 session 注册表，key = server_name, value = _SessionContext
_sessions: dict[str, _SessionContext] = {}


def _extract_result_text(result: Any) -> str:
    """从 CallToolResult 中提取文本内容。"""
    texts = []
    for c in result.content:
        if hasattr(c, "text"):
            prefix = "[ERROR] " if result.isError else ""
            texts.append(f"{prefix}{c.text}")
    return "\n".join(texts) if texts else str(result)


def _call_mcp_tool(server_name: str, tool_name: str, arguments: dict) -> str:
    """同步调用 MCP 工具，由 AgentTool.func 触发。

    所有 MCP 调用提交到后台事件循环线程执行，避免跨 loop 使用 session。
    """
    ctx = _sessions.get(server_name)
    if ctx is None or ctx.session is None:
        return f"错误: MCP Server '{server_name}' 未连接或已断开"

    async def _call():
        return await ctx.session.call_tool(tool_name, arguments)

    if ctx.loop_thread_id == threading.get_ident():
        return f"错误: 不支持在 MCP 后台线程中同步调用工具 [{tool_name}]"

    future = asyncio.run_coroutine_threadsafe(_call(), ctx.loop)
    try:
        result = future.result(timeout=60)
    except Exception as e:
        return f"MCP 工具调用错误 [{tool_name}]: {e}"
    return _extract_result_text(result)


def _json_schema_to_params(input_schema: dict) -> list[dict[str, Any]]:
    """将 MCP 工具的 JSON Schema inputSchema 转换为 AgentTool 的 parameters 格式。"""
    params = []
    properties = input_schema.get("properties", {})
    required_fields = input_schema.get("required", [])

    for name, schema in properties.items():
        json_type = schema.get("type", "string")
        if isinstance(json_type, list):
            type_str = ", ".join(t for t in json_type if t != "null")
        else:
            type_str = str(json_type)

        param = {
            "name": name,
            "type": type_str,
            "description": schema.get("description", ""),
        }
        if name in required_fields:
            param["required"] = True
        params.append(param)

    return params


def wrap_mcp_tools(mcp_tools: list[Any], server_name: str) -> list[Any]:
    """将一批 MCP Tool 对象包装为 AgentTool 列表。

    每个 AgentTool 命名为 mcp__<server_name>__<tool_name>，
    避免与内置工具名称冲突。
    """
    from graph_agent.tools.base import AgentTool

    agents = []
    for t in mcp_tools:
        tool_name = f"mcp__{server_name}__{t.name}"
        params = _json_schema_to_params(t.inputSchema)
        description = t.description or f"MCP 工具: {t.name} (来自 Server '{server_name}')"

        def make_func(srv_name: str, mcp_tool_name: str):
            def tool_func(**kwargs) -> str:
                return _call_mcp_tool(srv_name, mcp_tool_name, kwargs)
            return tool_func

        agent = AgentTool(
            name=tool_name,
            description=description,
            func=make_func(server_name, t.name),
            parameters=params,
        )
        agents.append(agent)

    return agents
