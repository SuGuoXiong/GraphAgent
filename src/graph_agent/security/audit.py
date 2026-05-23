"""审计日志模型、缓冲写入器、完整性校验。"""

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class AuditRecord:
    """单条审计日志记录。"""

    timestamp: str = ""
    call_sequence: int = 0
    subject: str = ""
    session_id: str = ""
    tool_name: str = ""
    action: str = ""
    resource: str = ""
    risk_level: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    status: str = ""          # "pending" / "allowed" / "denied" / "error"
    result_summary: str = ""
    error_message: str = ""
    escalated: bool = False
    escalation_approved: bool = False
    escalation_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "call_sequence": self.call_sequence,
            "subject": self.subject,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "action": self.action,
            "resource": self.resource,
            "risk_level": self.risk_level,
            "parameters": self.parameters,
            "status": self.status,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
            "escalated": self.escalated,
            "escalation_approved": self.escalation_approved,
            "escalation_reason": self.escalation_reason,
        }

    @staticmethod
    def from_dict(data: dict) -> "AuditRecord":
        return AuditRecord(
            timestamp=data.get("timestamp", ""),
            call_sequence=data.get("call_sequence", 0),
            subject=data.get("subject", ""),
            session_id=data.get("session_id", ""),
            tool_name=data.get("tool_name", ""),
            action=data.get("action", ""),
            resource=data.get("resource", ""),
            risk_level=data.get("risk_level", ""),
            parameters=data.get("parameters", {}),
            status=data.get("status", ""),
            result_summary=data.get("result_summary", ""),
            error_message=data.get("error_message", ""),
            escalated=data.get("escalated", False),
            escalation_approved=data.get("escalation_approved", False),
            escalation_reason=data.get("escalation_reason", ""),
        )

    @staticmethod
    def _sanitize_params(params: dict) -> dict:
        """脱敏参数中的敏感字段。"""
        sanitized = {}
        sensitive_keys = {"password", "token", "api_key", "secret", "auth"}
        for k, v in params.items():
            if any(sk in k.lower() for sk in sensitive_keys):
                sanitized[k] = "***REDACTED***"
            elif isinstance(v, str) and len(v) > 500:
                sanitized[k] = v[:500] + "..."
            else:
                sanitized[k] = v
        return sanitized

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."


class AuditLogger:
    """审计日志写入器（线程安全，缓冲写入）。

    缓冲策略：审计记录先写入内存缓冲区，达到 batch_size 或 flush() 调用时刷盘。
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, log_dir: str = "data/audit"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, log_dir: str = "data/audit"):
        if self._initialized:
            return
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._max_size = 100 * 1024 * 1024  # 100MB
        self._retention_days = 30
        self._buffer: list[AuditRecord] = []
        self._buffer_max = 50
        self._sequence = 0
        self._initialized = True

    def _get_log_path(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        base = self._log_dir / f"audit_{today}.jsonl"

        if base.exists() and base.stat().st_size >= self._max_size:
            i = 2
            while True:
                alt = self._log_dir / f"audit_{today}.{i}.jsonl"
                if not alt.exists() or alt.stat().st_size < self._max_size:
                    return alt
                i += 1
        return base

    def write(self, record: AuditRecord):
        with self._lock:
            self._sequence += 1
            record.call_sequence = self._sequence
            self._buffer.append(record)
            if len(self._buffer) >= self._buffer_max:
                self._flush_locked()

    def flush(self):
        with self._lock:
            self._flush_locked()

    def _flush_locked(self):
        if not self._buffer:
            return
        path = self._get_log_path()
        with open(path, "a", encoding="utf-8") as f:
            for record in self._buffer:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        self._buffer.clear()

    def cleanup(self):
        self.flush()
        cutoff = datetime.now(timezone.utc).timestamp() - self._retention_days * 86400
        for f in self._log_dir.glob("audit_*.jsonl*"):
            if f.stat().st_mtime < cutoff:
                f.unlink()


def get_audit_logger() -> AuditLogger:
    """获取全局 AuditLogger 单例。"""
    return AuditLogger()
