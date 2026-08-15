"""MCP 管理器——负责 MCP Server 的连接、工具发现和生命周期管理。

在后台线程中运行事件循环，维持所有 MCP 长连接。

生命周期约束（重要）：
    MCP stdio transport 内部使用 anyio 的 task group / cancel scope，
    其 ``__aenter__`` 与 ``__aexit__`` 必须发生在**同一个 asyncio task** 内，
    否则 anyio 会抛 ``RuntimeError: Attempted to exit cancel scope in a
    different task than it was entered in``。

    因此每个 MCP 连接都由一个独立的长驻 task 托管（``_run_connection``），
    在该 task 内完成 enter → 常驻 → exit 的完整生命周期；shutdown 时通过
    ``asyncio.Event`` 通知各 task 自行退出，而非在别的 task 里直接关闭。
"""

from __future__ import annotations

import asyncio
import atexit
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
        self._shutdown_event: asyncio.Event | None = None
        self._connection_tasks: dict[str, asyncio.Task] = {}

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

        # 进程退出时优雅关闭，避免 async generator 在别的 task 中 finalize
        atexit.register(self.shutdown)

    def shutdown(self) -> None:
        """停止后台事件循环，释放所有 MCP 连接。

        先通知各连接 task 退出（在同一 task 内 __aexit__），再停止事件循环。
        幂等：重复调用会因 ``self._loop is None`` 直接返回。
        """
        if self._loop is None:
            return

        try:
            # 通知所有连接 task 自行退出
            if self._shutdown_event is not None:
                self._loop.call_soon_threadsafe(self._shutdown_event.set)

            # 等待连接 task 完成清理（各自在同一 task 内 __aexit__）
            tasks = list(self._connection_tasks.values())
            if tasks:
                async def _wait() -> None:
                    await asyncio.gather(*tasks, return_exceptions=True)

                fut = asyncio.run_coroutine_threadsafe(_wait(), self._loop)
                fut.result(timeout=10)
        except Exception:
            pass

        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5)

        _sessions.clear()
        self._loop.close()
        self._loop = None
        self._loop_thread = None
        self._shutdown_event = None
        self._connection_tasks = {}

    # ── 内部方法 ──────────────────────────────────────────────

    def _run_event_loop(self) -> None:
        """后台线程入口：运行事件循环。"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _async_setup(self, configs: list) -> None:
        """异步初始化所有 MCP Server 连接。

        为每个连接启动一个长驻 task（负责 enter → 常驻 → exit），
        并等待所有连接就绪（成功或失败）后返回。
        """
        self._shutdown_event = asyncio.Event()
        self._connection_tasks = {}

        ready_events: dict[str, asyncio.Event] = {}
        for cfg in configs:
            ready = asyncio.Event()
            ready_events[cfg.name] = ready
            task = self._loop.create_task(self._run_connection(cfg, ready))
            self._connection_tasks[cfg.name] = task

        if ready_events:
            await asyncio.gather(*[ev.wait() for ev in ready_events.values()])

    async def _run_connection(self, cfg, ready: asyncio.Event) -> None:
        """单个 MCP 连接的完整生命周期。

        关键：``transport_ctx.__aenter__`` 与 ``__aexit__`` 都在本 task 内执行，
        规避 anyio cancel scope 跨 task 退出的 RuntimeError。
        """
        transport_ctx = None
        session = None

        try:
            try:
                # 1. 建立 transport（未进入）并进入，随后建立并进入 session
                if cfg.transport == TransportType.STDIO:
                    transport_ctx = self._build_stdio_context(cfg)
                else:
                    transport_ctx = self._build_http_context(cfg)

                entered = await transport_ctx.__aenter__()
                read, write = entered[0], entered[1]

                from mcp.client.session import ClientSession

                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()

                # 2. 发现并注册工具
                result = await session.list_tools()
                tools = wrap_mcp_tools(result.tools, cfg.name, cfg.risk_overrides)
                for tool in tools:
                    self._tool_center.register(tool)

                # 3. 记录到全局 session 注册表，供工具调用时桥接
                ctx = _SessionContext()
                ctx.session = session
                ctx._transport_ctx = transport_ctx
                ctx._session_ctx = session
                ctx.loop = self._loop
                ctx.loop_thread_id = self._loop_thread.ident if self._loop_thread else None
                _sessions[cfg.name] = ctx

                get_tracer().trace_phase(
                    f"MCP Server '{cfg.name}' 已连接",
                    "MCPManager",
                    f"{len(tools)} 个工具已注册",
                )
            except Exception as e:
                get_tracer().trace_error(
                    "MCP setup",
                    f"连接 MCP Server '{cfg.name}' 失败: {e}",
                )
            finally:
                ready.set()

            # 4. 常驻，直到 shutdown 通知退出
            await self._shutdown_event.wait()
        finally:
            # 5. 在同一 task 内退出上下文（session 先于 transport）
            if session is not None:
                try:
                    await session.__aexit__(None, None, None)
                except Exception:
                    pass
            if transport_ctx is not None:
                try:
                    await transport_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            _sessions.pop(cfg.name, None)

    def _build_stdio_context(self, cfg):
        """构造 stdio transport 的未进入异步上下文管理器。"""
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env if cfg.env else None,
            cwd=cfg.cwd,
        )
        return stdio_client(params)

    def _build_http_context(self, cfg):
        """构造 streamable-http transport 的未进入异步上下文管理器。"""
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(
            url=cfg.url,
            headers=cfg.headers,
            timeout=cfg.timeout,
            sse_read_timeout=cfg.sse_read_timeout,
        )
