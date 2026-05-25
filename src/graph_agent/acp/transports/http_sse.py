"""HTTP + SSE 传输实现 —— 基于 FastAPI + sse-starlette。

提供两个端点:
    POST /acp/message    — 发送请求/命令，返回同步 ack/error
    GET  /acp/events     — 建立 SSE 长连接，接收流式事件
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from graph_agent.acp.protocol import (
    ACPMessage,
    ACPConfig,
    RequestEvent,
    ResponseEvent,
    ErrorCode,
)
from graph_agent.acp.server import ACPServer
from graph_agent.acp.transports.base import ACPTransport, RequestHandler


class HTTPSSETransport(ACPTransport):
    """HTTP + SSE 传输实现。

    启动 FastAPI + uvicorn 服务，提供 REST 端点供客户端调用。
    SSE 通道通过 sse-starlette 的 EventSourceResponse 实现。

    使用方式:
        transport = HTTPSSETransport(acp_server, config)
        await transport.start()
    """

    def __init__(self, server: ACPServer, config: ACPConfig | None = None):
        self._server = server
        self._config = config or server.config
        self._handler: RequestHandler | None = None
        self._app = None
        self._server_task = None
        self._event_queues: dict[str, asyncio.Queue[ACPMessage]] = {}
        self._executing: set[str] = set()  # 正在执行中的 session_id

    # ── ACPTransport 接口实现 ─────────────────────────────

    async def start(self) -> None:
        self._build_app()
        import uvicorn
        uv_config = uvicorn.Config(
            self._app,
            host=self._config.host,
            port=self._config.port,
            log_level="warning",
        )
        uv_server = uvicorn.Server(uv_config)
        self._server_task = uv_server
        await uv_server.serve()

    async def stop(self) -> None:
        if self._server_task:
            self._server_task.should_exit = True
            self._server_task = None
        for q in self._event_queues.values():
            await q.put(ACPMessage.event("heartbeat", {"reason": "shutdown"}))

    def set_handler(self, handler: RequestHandler) -> None:
        self._handler = handler

    # ── FastAPI 应用构建 ──────────────────────────────────

    def _build_app(self) -> None:
        from fastapi import FastAPI, Body, Query, Request
        from fastapi.responses import JSONResponse
        from fastapi.middleware.cors import CORSMiddleware
        from sse_starlette.sse import EventSourceResponse

        app = FastAPI(title="GraphAgent ACP Server", version="1.0")

        if self._config.cors_origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=self._config.cors_origins,
                allow_methods=self._config.cors_methods,
                allow_headers=["*"],
            )

        server = self._server

        @app.post("/acp/message")
        async def acp_message(body: dict = Body(...)):
            """接收客户端请求并返回同步回复。

            对于 send_message 请求，先返回 ack，
            事件通过 SSE 通道异步推送。
            """

            acp_msg = ACPMessage.from_dict(body)

            # send_message: 异步执行，先返回 ack
            if acp_msg.event == RequestEvent.SEND_MESSAGE.value:
                session_id = acp_msg.payload.get("session_id", "")
                content = acp_msg.payload.get("content", "")

                if not session_id:
                    return JSONResponse(
                        _error_body(ErrorCode.INVALID_REQUEST, "缺少 session_id"),
                        status_code=400,
                    )

                if session_id not in self._executing:
                    self._executing.add(session_id)
                    asyncio.create_task(
                        self._execute_and_push(session_id, content, acp_msg.id)
                    )
                return JSONResponse(
                    ACPMessage.ack(acp_msg.id, "accepted").to_dict(),
                    status_code=202,
                )

            # resume_session: 异步恢复执行
            if acp_msg.event == RequestEvent.RESUME_SESSION.value:
                session_id = acp_msg.payload.get("session_id", "")

                if not session_id:
                    return JSONResponse(
                        _error_body(ErrorCode.INVALID_REQUEST, "缺少 session_id"),
                        status_code=400,
                    )

                if session_id not in self._executing:
                    self._executing.add(session_id)
                    asyncio.create_task(
                        self._resume_and_push(session_id, acp_msg.id)
                    )
                return JSONResponse(
                    ACPMessage.ack(acp_msg.id, "resume_accepted").to_dict(),
                    status_code=202,
                )

            # reply_user: 存储回答并异步恢复执行
            if acp_msg.event == RequestEvent.REPLY_USER.value:
                session_id = acp_msg.payload.get("session_id", "")

                if not session_id:
                    return JSONResponse(
                        _error_body(ErrorCode.INVALID_REQUEST, "缺少 session_id"),
                        status_code=400,
                    )

                # 先同步存储用户回答
                reply = server.handle_request(acp_msg)
                if reply.is_error():
                    code = ErrorCode(reply.payload.get("code", "INTERNAL_ERROR"))
                    status = _error_http_status(code)
                    return JSONResponse(reply.to_dict(), status_code=status)

                # 异步恢复执行
                if session_id not in self._executing:
                    self._executing.add(session_id)
                    asyncio.create_task(
                        self._resume_and_push(session_id, acp_msg.id)
                    )
                return JSONResponse(reply.to_dict(), status_code=202)

            # 其他请求: 同步处理
            reply = server.handle_request(acp_msg)
            status = 200
            if reply.is_error():
                code = ErrorCode(reply.payload.get("code", "INTERNAL_ERROR"))
                status = _error_http_status(code)
            return JSONResponse(reply.to_dict(), status_code=status)

        @app.get("/acp/events")
        async def acp_events(session_id: str = Query(...)):
            """SSE 事件流 —— 客户端通过此端点接收流式推送。

            为每个 session_id 建立独立的事件队列，
            服务端执行完成后将关闭对应 SSE 通道。
            """
            queue_id = f"{session_id}_{uuid.uuid4().hex[:6]}"
            q: asyncio.Queue[ACPMessage] = asyncio.Queue()
            self._event_queues[queue_id] = q

            async def event_generator():
                try:
                    while True:
                        try:
                            msg = await asyncio.wait_for(q.get(), timeout=15.0)
                        except asyncio.TimeoutError:
                            yield {"event": "heartbeat", "data": "{}"}
                            continue
                        if msg.event == "heartbeat" and msg.payload.get("reason") == "shutdown":
                            break
                        event_name = msg.event
                        # 执行完成或错误时发送事件后断连
                        if msg.event in ("execution_complete", "error"):
                            yield {"event": event_name, "data": msg.to_json()}
                            break
                        # execution_paused / ask_user: 发送事件但不断连
                        elif msg.event in ("execution_paused", "ask_user"):
                            yield {"event": event_name, "data": msg.to_json()}
                        elif msg.event == "execution_resumed":
                            yield {"event": event_name, "data": msg.to_json()}
                        elif msg.event == "final_answer":
                            yield {"event": event_name, "data": msg.to_json()}
                        else:
                            yield {"event": event_name, "data": msg.to_json()}
                except asyncio.CancelledError:
                    pass
                finally:
                    self._event_queues.pop(queue_id, None)

            return EventSourceResponse(event_generator())

        @app.get("/health")
        async def health():
            return {"status": "ok", "active_sessions": server.session_manager.active_count}

        @app.get("/acp/sessions/{session_id}/messages")
        async def get_session_messages(session_id: str):
            """返回指定会话的所有消息（供 Web UI 加载历史对话）。"""
            ctx = server.session_manager.get_context(session_id)
            if ctx is None:
                ctx = server.session_manager.load_session(session_id)
            if ctx is None:
                return JSONResponse({"error": "会话不存在"}, status_code=404)

            from graph_agent.session.persistence import _serialize_message
            messages = [_serialize_message(m) for m in ctx.history.messages]
            result = {
                "session_id": session_id,
                "turn_count": ctx.history.turn_count,
                "messages": messages,
                "status": ctx.status,
            }
            if ctx.checkpoint:
                result["checkpoint"] = {
                    "phase": ctx.checkpoint.get("phase", ""),
                    "recovery_hint": ctx.checkpoint.get("recovery_hint", ""),
                }
            return JSONResponse(result)

        self._app = app

    async def _execute_and_push(self, session_id: str, content: str, request_id: str) -> None:
        """执行编排并推送事件到所有关联的 SSE 队列。

        实时事件通过 _broadcast 闭包在工作线程中直接推送；
        终端事件（final_answer、execution_complete 等）通过返回值推送。
        """
        queues_snapshot = list(self._event_queues.items())

        def _broadcast(msg: ACPMessage) -> None:
            prefix = f"{session_id}_"
            for key, q in queues_snapshot:
                if key.startswith(prefix):
                    try:
                        q.put_nowait(msg)
                    except asyncio.QueueFull:
                        pass

        try:
            events = await self._server.execute_turn(
                session_id, content,
                live_push=_broadcast,
            )
            # 推送终端事件（final_answer、execution_complete 等）
            prefix = f"{session_id}_"
            for key, q in self._event_queues.items():
                if key.startswith(prefix):
                    for event in events:
                        try:
                            q.put_nowait(event)
                        except asyncio.QueueFull:
                            pass
        finally:
            self._executing.discard(session_id)

    async def _resume_and_push(self, session_id: str, request_id: str) -> None:
        """恢复暂停的会话并推送事件到所有关联的 SSE 队列。"""
        queues_snapshot = list(self._event_queues.items())

        def _broadcast(msg: ACPMessage) -> None:
            prefix = f"{session_id}_"
            for key, q in queues_snapshot:
                if key.startswith(prefix):
                    try:
                        q.put_nowait(msg)
                    except asyncio.QueueFull:
                        pass

        try:
            events = await self._server.resume_session(
                session_id,
                live_push=_broadcast,
            )
            prefix = f"{session_id}_"
            for key, q in self._event_queues.items():
                if key.startswith(prefix):
                    for event in events:
                        try:
                            q.put_nowait(event)
                        except asyncio.QueueFull:
                            pass
        finally:
            self._executing.discard(session_id)


def _error_body(code: ErrorCode, message: str, recoverable: bool = True) -> dict[str, Any]:
    return ACPMessage.error(code, message, recoverable=recoverable).to_dict()


def _error_http_status(code: ErrorCode) -> int:
    from graph_agent.acp.protocol import error_http_status
    return error_http_status(code)
