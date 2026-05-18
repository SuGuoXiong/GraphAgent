"""会话管理器 —— 会话的完整生命周期管理。

封装 ConversationHistory + ConversationPersistence + 两级压缩，
为 ACPServer 提供统一的会话创建、加载、持久化、压缩接口。
"""

from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from graph_agent.session.compressor import (
    SessionConfig,
    PriorityCompressor,
    SummaryCompressor,
)
from graph_agent.session.history import ConversationHistory
from graph_agent.session.persistence import ConversationPersistence
from graph_agent.acp.protocol import (
    ACPConfig,
    CompressionResult,
    SessionInfo,
    ErrorCode,
)

if TYPE_CHECKING:
    from graph_agent.llm.base import LLMProvider


@dataclass
class ConversationContext:
    """会话上下文的运行时包装 —— 将对话历史与会话配置绑定。"""
    history: ConversationHistory
    config: SessionConfig
    created_at: str = ""
    last_active_at: str = ""

    def touch(self, timestamp: str = "") -> None:
        import datetime
        self.last_active_at = timestamp or datetime.datetime.now(
            datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")


class SessionManager:
    """会话管理器 —— 多会话并发管理。

    职责:
    1. 创建/加载/删除会话
    2. 管理活跃会话的运行时状态
    3. 压缩检查与执行
    4. 持久化调度
    """

    def __init__(self, config: ACPConfig | None = None):
        self._acp_config = config or ACPConfig()
        self._active: dict[str, ConversationContext] = {}
        self._session_config = SessionConfig.from_yaml()
        self._persistence = ConversationPersistence(
            self._acp_config.storage_dir or self._session_config.storage_dir
        )
        self._priority_compressor = PriorityCompressor(self._session_config)
        self._summary_compressor = SummaryCompressor(self._session_config)
        self._lock = {}  # per-session asyncio locks

    # ── 会话 CRUD ──────────────────────────────────────────

    def create_session(self, overrides: dict | None = None) -> str:
        """创建新会话，返回 session_id。"""
        history = ConversationHistory()
        ctx = ConversationContext(
            history=history,
            config=self._session_config,
            created_at=history.created_at,
            last_active_at=history.created_at,
        )
        self._active[history.session_id] = ctx
        self._enforce_session_limit()
        return history.session_id

    def load_session(self, session_id: str) -> ConversationContext | None:
        """从磁盘加载已有会话到活跃列表。"""
        if session_id in self._active:
            ctx = self._active[session_id]
            ctx.touch()
            return ctx

        history = self._persistence.load(session_id)
        if history is None:
            return None

        ctx = ConversationContext(
            history=history,
            config=self._session_config,
            created_at=history.created_at,
            last_active_at=history.updated_at or history.created_at,
        )
        self._active[session_id] = ctx
        self._enforce_session_limit()
        return ctx

    def get_context(self, session_id: str) -> ConversationContext | None:
        """获取活跃会话上下文（不触发磁盘加载）。"""
        ctx = self._active.get(session_id)
        if ctx:
            ctx.touch()
        return ctx

    def save_session(self, session_id: str) -> str | None:
        """持久化指定会话到磁盘。返回文件路径，会话不存在返回 None。"""
        ctx = self._active.get(session_id)
        if ctx is None:
            return None
        return self._persistence.save(ctx.history)

    def delete_session(self, session_id: str) -> bool:
        """删除会话（内存 + 磁盘）。"""
        if session_id in self._active:
            del self._active[session_id]
        file_path = Path(self._acp_config.storage_dir) / f"{session_id}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def list_sessions(self) -> list[SessionInfo]:
        """列出所有已持久化的会话摘要。"""
        storage = Path(self._acp_config.storage_dir)
        if not storage.exists():
            return []

        results: list[SessionInfo] = []
        for fpath in sorted(storage.glob("*.json"), key=os.path.getmtime, reverse=True):
            sid = fpath.stem
            try:
                history = self._persistence.load(sid)
                if history:
                    results.append(SessionInfo.from_history(
                        session_id=history.session_id,
                        messages=history.messages,
                        created_at=history.created_at,
                        updated_at=history.updated_at,
                        turn_count=history.turn_count,
                    ))
            except Exception:
                continue
        return results

    def session_exists(self, session_id: str) -> bool:
        if session_id in self._active:
            return True
        return (Path(self._acp_config.storage_dir) / f"{session_id}.json").exists()

    # ── 压缩 ──────────────────────────────────────────────

    def compress_if_needed(self, session_id: str) -> CompressionResult:
        """检查并执行压缩。"""
        ctx = self._active.get(session_id)
        if ctx is None:
            return CompressionResult()

        history = ctx.history
        token_count = history.estimate_tokens()
        before_count = len(history.messages)

        # 普通压缩
        if token_count > self._session_config.normal_threshold_tokens:
            compressed = self._priority_compressor.compress(history)
            history.replace_messages(compressed)
            after_count = len(history.messages)
            after_tokens = history.estimate_tokens()
            if after_count < before_count:
                return CompressionResult(
                    did_compress=True,
                    before_count=before_count,
                    after_count=after_count,
                    before_tokens=token_count,
                    after_tokens=after_tokens,
                    level="priority",
                )

        # 高度压缩
        token_count = history.estimate_tokens()
        if token_count > self._session_config.aggressive_threshold_tokens:
            try:
                from graph_agent.llm import LLMFactory
                provider = LLMFactory.create_from_env()
                compressed = self._summary_compressor.compress(history, provider)
                history.replace_messages(compressed)
                after_count = len(history.messages)
                after_tokens = history.estimate_tokens()
                if after_count < before_count:
                    return CompressionResult(
                        did_compress=True,
                        before_count=before_count,
                        after_count=after_count,
                        before_tokens=token_count,
                        after_tokens=after_tokens,
                        level="summary",
                    )
            except Exception:
                return CompressionResult()

        return CompressionResult(
            before_count=before_count,
            after_count=before_count,
            before_tokens=token_count,
            after_tokens=token_count,
        )

    # ── 清理 ──────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """清理过期会话（超过 session_timeout 未活动），返回清理数量。"""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        threshold = now.timestamp() - self._acp_config.session_timeout
        expired = []
        for sid, ctx in self._active.items():
            try:
                last = datetime.datetime.strptime(
                    ctx.last_active_at, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=datetime.timezone.utc)
                if last.timestamp() < threshold:
                    expired.append(sid)
            except (ValueError, TypeError):
                expired.append(sid)
        for sid in expired:
            self.save_session(sid)
            del self._active[sid]
        return len(expired)

    def _enforce_session_limit(self) -> None:
        if len(self._active) <= self._acp_config.max_sessions:
            return
        sorted_sessions = sorted(
            self._active.items(),
            key=lambda kv: kv[1].last_active_at,
        )
        to_remove = sorted_sessions[:len(self._active) - self._acp_config.max_sessions]
        for sid, ctx in to_remove:
            self.save_session(sid)
            del self._active[sid]

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def storage_dir(self) -> str:
        return str(Path(self._acp_config.storage_dir).resolve())
