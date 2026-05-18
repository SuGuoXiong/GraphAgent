"""ACP 协议消息类型定义 —— 协议层的基础数据结构和错误码。

所有 ACP 消息均遵循统一的 JSON 信封格式:
    {"version":"1.0","type":"request|response|event","event":"...","id":"...","timestamp":"...","payload":{}}
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

PROTOCOL_VERSION = "1.0"
MAX_CONTENT_LENGTH = 10000

# 默认请求和响应超时
DEFAULT_TIMEOUT_SECONDS = 120


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ── 事件类型枚举 ──────────────────────────────────────────────


class RequestEvent(str, Enum):
    """客户端 → 服务端的请求事件类型。"""
    SEND_MESSAGE = "send_message"
    CREATE_SESSION = "create_session"
    LOAD_SESSION = "load_session"
    INTERRUPT = "interrupt"
    CONFIGURE = "configure"
    LIST_SESSIONS = "list_sessions"
    DELETE_SESSION = "delete_session"


class ResponseEvent(str, Enum):
    """服务端 → 客户端的同步回复事件类型。"""
    ACK = "ack"
    ERROR = "error"
    SESSION_CREATED = "session_created"
    SESSION_LOADED = "session_loaded"
    SESSION_LIST = "session_list"
    SESSION_DELETED = "session_deleted"


class PushEvent(str, Enum):
    """服务端 → 客户端的异步推送事件类型。"""
    PHASE_CHANGED = "phase_changed"
    AGENT_MESSAGE = "agent_message"
    LLM_STREAM_START = "llm_stream_start"
    LLM_STREAM_CHUNK = "llm_stream_chunk"
    LLM_STREAM_END = "llm_stream_end"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINAL_ANSWER = "final_answer"
    EXECUTION_COMPLETE = "execution_complete"
    HEARTBEAT = "heartbeat"


# ── 错误码 ────────────────────────────────────────────────────


class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_LIMIT = "SESSION_LIMIT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    COMPRESSION_ERROR = "COMPRESSION_ERROR"
    LLM_ERROR = "LLM_ERROR"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.SESSION_NOT_FOUND: 404,
    ErrorCode.SESSION_EXPIRED: 410,
    ErrorCode.SESSION_LIMIT: 429,
    ErrorCode.EXECUTION_ERROR: 500,
    ErrorCode.COMPRESSION_ERROR: 500,
    ErrorCode.LLM_ERROR: 502,
    ErrorCode.TRANSPORT_ERROR: 500,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.INTERNAL_ERROR: 500,
}


def error_http_status(code: ErrorCode) -> int:
    return _ERROR_HTTP_STATUS.get(code, 500)


# ── 核心消息类型 ──────────────────────────────────────────────


@dataclass
class ACPMessage:
    """ACP 协议消息 —— 统一的 JSON 信封。

    所有客户端请求、服务端回复、异步事件均使用此结构，
    通过 type + event 字段区分消息角色。
    """

    type: str  # "request" | "response" | "event"
    event: str  # 事件名称（见 RequestEvent / ResponseEvent / PushEvent）
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=_new_id)
    version: str = PROTOCOL_VERSION
    timestamp: str = field(default_factory=_iso_now)

    # ── 工厂方法 ──────────────────────────────────────────

    @classmethod
    def request(cls, event: str | RequestEvent, payload: dict[str, Any] | None = None) -> "ACPMessage":
        """创建客户端请求消息。"""
        return cls(
            type="request",
            event=event.value if isinstance(event, RequestEvent) else event,
            payload=payload or {},
        )

    @classmethod
    def response(cls, event: str | ResponseEvent, payload: dict[str, Any] | None = None,
                 request_id: str = "") -> "ACPMessage":
        """创建服务端同步回复消息。"""
        return cls(
            type="response",
            event=event.value if isinstance(event, ResponseEvent) else event,
            payload=payload or {},
            id=request_id or _new_id(),
        )

    @classmethod
    def event(cls, event: str | PushEvent, payload: dict[str, Any] | None = None) -> "ACPMessage":
        """创建服务端异步推送事件。"""
        return cls(
            type="event",
            event=event.value if isinstance(event, PushEvent) else event,
            payload=payload or {},
        )

    @classmethod
    def error(cls, code: ErrorCode, message: str, request_id: str = "",
              recoverable: bool = True, detail: dict[str, Any] | None = None) -> "ACPMessage":
        """创建服务端错误回复。"""
        return cls.response(
            ResponseEvent.ERROR,
            {
                "code": code.value,
                "message": message,
                "detail": detail or {},
                "recoverable": recoverable,
            },
            request_id=request_id,
        )

    @classmethod
    def ack(cls, request_id: str, status: str = "accepted") -> "ACPMessage":
        """创建确认回复。"""
        return cls.response(
            ResponseEvent.ACK,
            {"request_id": request_id, "status": status},
            request_id=request_id,
        )

    # ── 序列化 ────────────────────────────────────────────

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type,
            "event": self.event,
            "id": self.id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @classmethod
    def from_json(cls, raw: str) -> "ACPMessage":
        data = json.loads(raw)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ACPMessage":
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            type=data.get("type", "event"),
            event=data.get("event", ""),
            id=data.get("id", _new_id()),
            timestamp=data.get("timestamp", _iso_now()),
            payload=data.get("payload", {}),
        )

    # ── 辅助方法 ──────────────────────────────────────────

    def is_request(self) -> bool:
        return self.type == "request"

    def is_response(self) -> bool:
        return self.type == "response"

    def is_event(self) -> bool:
        return self.type == "event"

    def is_error(self) -> bool:
        return self.type == "response" and self.event == ResponseEvent.ERROR.value

    def get_request_event(self) -> RequestEvent | None:
        if self.is_request():
            try:
                return RequestEvent(self.event)
            except ValueError:
                return None
        return None


# ── ACP 配置 ──────────────────────────────────────────────────


@dataclass
class ACPConfig:
    """ACP 服务端配置。"""
    host: str = "127.0.0.1"
    port: int = 8080
    cors_origins: list[str] = field(default_factory=list)
    cors_methods: list[str] = field(default_factory=lambda: ["GET", "POST"])
    max_sessions: int = 50
    session_timeout: int = 1800  # 秒
    cleanup_interval: int = 300
    heartbeat_interval: int = 15
    transports: list[str] = field(default_factory=lambda: ["http_sse"])
    max_event_buffer: int = 1000
    storage_dir: str = "data/conversations"

    @property
    def session_timeout_minutes(self) -> int:
        return self.session_timeout // 60

    @classmethod
    def from_yaml(cls, path: str = "config/acp_config.yaml") -> "ACPConfig":
        config_path = Path(path)
        if not config_path.exists():
            return cls()
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        acp = data.get("acp", {})
        server = acp.get("server", {})
        session = acp.get("session", {})
        streaming = acp.get("streaming", {})
        cors = server.get("cors", {})
        return cls(
            host=server.get("host", "127.0.0.1"),
            port=server.get("port", 8080),
            cors_origins=cors.get("allowed_origins", []),
            cors_methods=cors.get("allowed_methods", ["GET", "POST"]),
            max_sessions=session.get("max_concurrent_sessions", 50),
            session_timeout=session.get("session_timeout_minutes", 30) * 60,
            cleanup_interval=session.get("cleanup_interval_minutes", 5) * 60,
            heartbeat_interval=streaming.get("heartbeat_interval_seconds", 15),
            transports=server.get("transports", ["http_sse"]),
            max_event_buffer=streaming.get("max_event_buffer", 1000),
            storage_dir=session.get("persistence", {}).get("storage_dir", "data/conversations"),
        )


# ── 会话摘要 ──────────────────────────────────────────────────


@dataclass
class SessionInfo:
    """会话摘要信息 —— 用于 list_sessions 返回。"""
    session_id: str
    created_at: str
    updated_at: str
    turn_count: int
    message_count: int
    preview: str  # 首条用户消息的前 100 字符

    @classmethod
    def from_history(cls, session_id: str, messages: list[Any],
                     created_at: str = "", updated_at: str = "",
                     turn_count: int = 0) -> "SessionInfo":
        preview = ""
        for m in messages:
            content = getattr(m, "content", "")
            if isinstance(content, str):
                preview = content[:100]
                break
            if isinstance(content, list) and content:
                first = content[0]
                t = getattr(first, "text", None)
                if t:
                    preview = t[:100]
                    break
        return cls(
            session_id=session_id,
            created_at=created_at,
            updated_at=updated_at,
            turn_count=turn_count,
            message_count=len(messages),
            preview=preview,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": self.turn_count,
            "message_count": self.message_count,
            "preview": self.preview,
        }


# ── 压缩结果 ──────────────────────────────────────────────────


@dataclass
class CompressionResult:
    """压缩操作的结果摘要。"""
    did_compress: bool = False
    before_count: int = 0
    after_count: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    level: str = ""  # "priority" | "summary"
