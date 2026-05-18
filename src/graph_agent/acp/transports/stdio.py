"""stdio 传输实现 —— 通过标准输入输出进行 ACP 通信。

每行一个 JSON 消息:
    < stdin:  {"type":"request","event":"send_message",...}
    > stdout: {"type":"response","event":"ack",...}

适合 CLI 工具和 IDE 插件使用。
"""

from __future__ import annotations

import asyncio
import json
import sys

from graph_agent.acp.protocol import ACPMessage, RequestEvent
from graph_agent.acp.server import ACPServer
from graph_agent.acp.transports.base import ACPTransport, RequestHandler


class StdioTransport(ACPTransport):
    """stdio 传输实现 —— 每行一个 JSON 消息。

    使用方式:
        transport = StdioTransport(acp_server)
        await transport.start()
    """

    def __init__(self, server: ACPServer):
        self._server = server
        self._handler: RequestHandler | None = None
        self._running = False
        self._reader_lock = asyncio.Lock()

    async def start(self) -> None:
        self._running = True
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._read_loop)

    async def stop(self) -> None:
        self._running = False

    def set_handler(self, handler: RequestHandler) -> None:
        self._handler = handler

    def _read_loop(self) -> None:
        """在同步线程中读取 stdin（避免 asyncio 阻塞问题）。"""
        for line in sys.stdin:
            if not self._running:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = ACPMessage.from_json(line)
            except json.JSONDecodeError:
                self._write_error("无法解析 JSON 消息")
                continue

            asyncio.run_coroutine_threadsafe(
                self._dispatch(msg), asyncio.get_event_loop()
            )

    async def _dispatch(self, request: ACPMessage) -> None:
        if request.event == RequestEvent.SEND_MESSAGE.value:
            session_id = request.payload.get("session_id", "")
            content = request.payload.get("content", "")
            if not session_id:
                self._write(ACPMessage.ack(request.id, "rejected: missing session_id"))
                return
            self._write(ACPMessage.ack(request.id, "accepted"))
            events = await self._server.execute_turn(session_id, content)
            for event in events:
                self._write(event)
        else:
            reply = self._server.handle_request(request)
            self._write(reply)

    def _write(self, msg: ACPMessage) -> None:
        sys.stdout.write(msg.to_json() + "\n")
        sys.stdout.flush()

    def _write_error(self, text: str) -> None:
        sys.stdout.write(
            ACPMessage.error(
                __import__('graph_agent.acp.protocol', fromlist=['ErrorCode']).ErrorCode.INVALID_REQUEST,
                text,
            ).to_json() + "\n"
        )
        sys.stdout.flush()
