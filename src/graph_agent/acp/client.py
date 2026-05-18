"""ACP 客户端 SDK —— UI 侧的协议封装。

为 UI 开发者提供简洁的异步 API，屏蔽底层传输差异。

使用示例:
    async with ACPClient.http("http://localhost:8080") as client:
        session_id = await client.create_session()
        async for event in client.send_message(session_id, "你好"):
            if event.event == "final_answer":
                print(event.payload["content"])
"""

from __future__ import annotations

import json
import sys
from typing import AsyncIterator

import aiohttp

from graph_agent.acp.protocol import (
    ACPMessage,
    RequestEvent,
    SessionInfo,
    PROTOCOL_VERSION,
    _new_id,
)


class ACPClient:
    """ACP 客户端 —— 封装传输层连接和消息序列化。

    支持三种连接方式:
        ACPClient.http("http://localhost:8080")
        ACPClient.stdio()  # 直接在进程中调用 ACPServer
    """

    def __init__(self):
        self._http_base: str | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._stdio_server: Any = None
        self._mode: str = ""  # "http" | "stdio"

    # ── 工厂方法 ──────────────────────────────────────────

    @classmethod
    def http(cls, base_url: str) -> "ACPClient":
        client = cls()
        client._http_base = base_url.rstrip("/")
        client._mode = "http"
        return client

    @classmethod
    def stdio(cls) -> "ACPClient":
        """创建 stdio 模式客户端 —— 在同一进程中直接调用 ACPServer。

        不经过网络传输，直接通过 Python 函数调用交互，
        适合 CLI 工具和调试场景。
        """
        from graph_agent.acp.server import ACPServer
        client = cls()
        client._stdio_server = ACPServer()
        client._mode = "stdio"
        return client

    # ── 生命周期 ──────────────────────────────────────────

    async def __aenter__(self) -> "ACPClient":
        if self._mode == "http":
            self._http_session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def close(self) -> None:
        if self._http_session:
            await self._http_session.close()
            self._http_session = None

    # ── 会话管理 ──────────────────────────────────────────

    async def create_session(self, config: dict | None = None) -> str:
        """创建新会话，返回 session_id。"""
        request = ACPMessage.request(RequestEvent.CREATE_SESSION, {
            "config": config,
        })
        reply = await self._send_request(request)
        return reply.payload.get("session_id", "")

    async def load_session(self, session_id: str) -> SessionInfo | None:
        """加载已有会话。"""
        request = ACPMessage.request(RequestEvent.LOAD_SESSION, {
            "session_id": session_id,
        })
        reply = await self._send_request(request)
        if reply.is_error():
            return None
        return SessionInfo(
            session_id=session_id,
            turn_count=reply.payload.get("turn_count", 0),
            message_count=reply.payload.get("message_count", 0),
            created_at=reply.payload.get("created_at", ""),
            updated_at=reply.payload.get("updated_at", ""),
            preview="",
        )

    async def list_sessions(self) -> list[SessionInfo]:
        """列出所有已持久化的会话。"""
        request = ACPMessage.request(RequestEvent.LIST_SESSIONS)
        reply = await self._send_request(request)
        sessions = reply.payload.get("sessions", [])
        return [SessionInfo(**s) for s in sessions]

    async def delete_session(self, session_id: str) -> bool:
        """删除指定会话。"""
        request = ACPMessage.request(RequestEvent.DELETE_SESSION, {
            "session_id": session_id,
        })
        reply = await self._send_request(request)
        return not reply.is_error()

    # ── 消息发送 ──────────────────────────────────────────

    async def send_message(
        self, session_id: str, content: str
    ) -> AsyncIterator[ACPMessage]:
        """发送用户消息，返回事件流异步迭代器。"""
        if self._mode == "http":
            async for event in self._send_message_http(session_id, content):
                yield event
        elif self._mode == "stdio":
            for event in await self._send_message_stdio(session_id, content):
                yield event

    async def _send_message_http(
        self, session_id: str, content: str
    ) -> AsyncIterator[ACPMessage]:
        """HTTP+SSE 模式: 发送 POST 请求后在 SSE 通道上接收事件。"""
        if not self._http_session or not self._http_base:
            return

        request = ACPMessage.request(RequestEvent.SEND_MESSAGE, {
            "session_id": session_id,
            "content": content,
        })
        request_id = request.id

        # 发送请求
        async with self._http_session.post(
            f"{self._http_base}/acp/message",
            json=request.to_dict(),
        ) as resp:
            if resp.status != 202:
                error_data = await resp.json()
                yield ACPMessage.from_dict(error_data)
                return

        # 建立 SSE 连接接收事件
        async with self._http_session.get(
            f"{self._http_base}/acp/events",
            params={"session_id": session_id},
        ) as resp:
            async for line in resp.content:
                line_text = line.decode("utf-8").strip()
                if not line_text or line_text.startswith(":"):
                    continue
                if line_text.startswith("data:"):
                    data = line_text[5:].strip()
                    if data == "{}":
                        continue
                    try:
                        yield ACPMessage.from_json(data)
                    except json.JSONDecodeError:
                        continue

    async def _send_message_stdio(
        self, session_id: str, content: str
    ) -> list[ACPMessage]:
        """stdio 模式: 直接调用 ACPServer.execute_turn()。"""
        if not self._stdio_server:
            return [ACPMessage.error(
                __import__('graph_agent.acp.protocol', fromlist=['ErrorCode']).ErrorCode.INTERNAL_ERROR,
                "Stdio server not initialized",
            )]
        return await self._stdio_server.execute_turn(session_id, content)

    # ── 内部方法 ──────────────────────────────────────────

    async def _send_request(self, request: ACPMessage) -> ACPMessage:
        if self._mode == "http" and self._http_session and self._http_base:
            async with self._http_session.post(
                f"{self._http_base}/acp/message",
                json=request.to_dict(),
            ) as resp:
                data = await resp.json()
                return ACPMessage.from_dict(data)
        elif self._mode == "stdio" and self._stdio_server:
            return self._stdio_server.handle_request(request)
        return ACPMessage.error(
            __import__('graph_agent.acp.protocol', fromlist=['ErrorCode']).ErrorCode.INTERNAL_ERROR,
            "Client not initialized",
        )
