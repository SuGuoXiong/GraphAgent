"""MCP 管理器——负责 MCP Server 的连接、工具发现和生命周期管理。

在后台线程中运行事件循环，维持所有 MCP 长连接。
"""

from __future__ import annotations

import asyncio
import threading

from graph_agent.mcp.config import load_mcp_config, TransportType
from graph_agent.mcp.tool_adapter import (
    _SessionContext,
    _sessions,
    wrap_mcp_tools,
)
from graph_agent.tools.base import ToolCenter
from graph_agent.tracer import get_tracer


class MCPManager:
    """MCP Server 生命周期管理器。

    在 GraphAgent 启动时调用 setup()，读取 mcp_servers.json，
    建立连接，发现工具，注册到 ToolCenter。
    """

    def __init__(self, tool_center: ToolCenter | None = None):
        self._tool_center = tool_center
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    def setup(self) -> None:
        """同步入口：读取配置、启动后台事件循环、建立所有 MCP 连接。"""
        configs = load_mcp_config()
        if not configs:
            return

        if self._tool_center is None:
            return

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_event_loop,
            name="mcp-event-loop",
            daemon=True,
        )
        self._loop_thread.start()

        future = asyncio.run_coroutine_threadsafe(
            self._async_setup(configs), self._loop
        )
        try:
            future.result(timeout=120)
        except Exception:
            get_tracer().trace_error(
                "MCP setup", "MCP 初始化超时或失败，部分 MCP Server 可能不可用"
            )

    def shutdown(self) -> None:
        """停止后台事件循环，释放所有 MCP 连接。

        MCP session 和 transport 上下文的创建/销毁必须发生在同一 asyncio task 中，
        否则 anyio 的 cancel scope 会抛出 RuntimeError。由于后台线程是 daemon 线程，
        随进程退出自然清理即可。这里只停止事件循环。
        """
        if self._loop is None:
            return

        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5)

        _sessions.clear()
        self._loop.close()
        self._loop = None
        self._loop_thread = None

    # ── 内部方法 ──────────────────────────────────────────────

    def _run_event_loop(self) -> None:
        """后台线程入口：运行事件循环。"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _async_setup(self, configs: list) -> None:
        """异步初始化所有 MCP Server 连接。"""
        for cfg in configs:
            try:
                await self._connect_server(cfg)
            except Exception as e:
                get_tracer().trace_error(
                    "MCP setup",
                    f"连接 MCP Server '{cfg.name}' 失败: {e}",
                )

    async def _connect_server(self, cfg) -> None:
        """连接单个 MCP Server，发现并注册其工具。"""
        tracer = get_tracer()

        if cfg.transport == TransportType.STDIO:
            transport_ctx, session_ctx, session = await self._connect_stdio(cfg)
        else:
            transport_ctx, session_ctx, session = await self._connect_http(cfg)

        result = await session.list_tools()
        tools = wrap_mcp_tools(result.tools, cfg.name)
        for tool in tools:
            self._tool_center.register(tool)

        ctx = _SessionContext()
        ctx.session = session
        ctx._transport_ctx = transport_ctx
        ctx._session_ctx = session_ctx
        ctx.loop = self._loop
        ctx.loop_thread_id = self._loop_thread.ident if self._loop_thread else None
        _sessions[cfg.name] = ctx

        tracer.trace_phase(
            f"MCP Server '{cfg.name}' 已连接",
            "MCPManager",
            f"{len(tools)} 个工具已注册",
        )

    async def _connect_stdio(self, cfg) -> tuple:
        """建立 stdio 类型的 MCP 连接。"""
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.session import ClientSession

        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env if cfg.env else None,
            cwd=cfg.cwd,
        )

        transport_ctx = stdio_client(params)
        read, write = await transport_ctx.__aenter__()

        session = ClientSession(read, write)
        session_ctx = session
        await session.__aenter__()
        await session.initialize()

        return transport_ctx, session_ctx, session

    async def _connect_http(self, cfg) -> tuple:
        """建立 streamable-http 类型的 MCP 连接。"""
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.client.session import ClientSession

        transport_ctx = streamablehttp_client(
            url=cfg.url,
            headers=cfg.headers,
            timeout=cfg.timeout,
            sse_read_timeout=cfg.sse_read_timeout,
        )
        read, write, _get_url = await transport_ctx.__aenter__()

        session = ClientSession(read, write)
        session_ctx = session
        await session.__aenter__()
        await session.initialize()

        return transport_ctx, session_ctx, session
