"""会话持久化 —— MessageBlock 的 JSON 序列化/反序列化。

支持 ContentBlock / ToolUseBlock / ToolResultBlock 的递归序列化，
以及原子写入（先写 .tmp 再 rename）。
"""

import json
import os
import re
from pathlib import Path
from typing import Any

from graph_agent.message.base import (
    ContentBlock,
    MessageBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from graph_agent.session.history import ConversationHistory

# 匹配无效代理对（lone surrogates），在序列化前清理
_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')


def sanitize_text(text: str) -> str:
    """移除文本中的无效代理对字符（lone surrogates），防止 UTF-8 编码失败。"""
    return _SURROGATE_RE.sub('', text)


def _serialize_content_block(block: ContentBlock) -> dict[str, Any]:
    """将单个 ContentBlock 序列化为 dict。"""
    result: dict[str, Any] = {"block_type": block.block_type}
    if block.text is not None:
        result["text"] = block.text
    if block.thinking is not None:
        result["thinking"] = block.thinking
    if block.tool_use is not None:
        result["tool_use"] = {
            "tool_id": block.tool_use.tool_id,
            "tool_name": block.tool_use.tool_name,
            "input_args": block.tool_use.input_args,
        }
    if block.tool_result is not None:
        result["tool_result"] = {
            "tool_id": block.tool_result.tool_id,
            "tool_name": block.tool_result.tool_name,
            "output": block.tool_result.output,
            "status": block.tool_result.status,
            "error_message": block.tool_result.error_message,
        }
    return result


def _serialize_message(msg: MessageBlock) -> dict[str, Any]:
    """将单个 MessageBlock 序列化为可 JSON 化的 dict。"""
    if isinstance(msg.content, str):
        serialized_content: str | list[dict[str, Any]] = msg.content
    else:
        serialized_content = [_serialize_content_block(b) for b in msg.content]
    return {
        "role": msg.role,
        "content": serialized_content,
        "name": msg.name,
        "message_type": msg.message_type,
        "message_id": msg.message_id,
        "metadata": msg.metadata or {},
    }


def _deserialize_content_block(data: dict[str, Any]) -> ContentBlock:
    """从 dict 反序列化 ContentBlock。"""
    tool_use = None
    if "tool_use" in data and data["tool_use"]:
        tu = data["tool_use"]
        tool_use = ToolUseBlock(
            tool_id=tu["tool_id"],
            tool_name=tu["tool_name"],
            input_args=tu.get("input_args", {}),
        )
    tool_result = None
    if "tool_result" in data and data["tool_result"]:
        tr = data["tool_result"]
        tool_result = ToolResultBlock(
            tool_id=tr["tool_id"],
            tool_name=tr["tool_name"],
            output=tr["output"],
            status=tr.get("status", "success"),
            error_message=tr.get("error_message"),
        )
    return ContentBlock(
        block_type=data["block_type"],
        text=data.get("text"),
        thinking=data.get("thinking"),
        tool_use=tool_use,
        tool_result=tool_result,
    )


def _deserialize_message(data: dict[str, Any]) -> MessageBlock:
    """从 dict 反序列化 MessageBlock。"""
    raw_content = data["content"]
    if isinstance(raw_content, str):
        content: str | list[ContentBlock] = raw_content
    else:
        content = [_deserialize_content_block(b) for b in raw_content]
    return MessageBlock(
        role=data["role"],
        content=content,
        name=data.get("name", ""),
        message_type=data["message_type"],
        message_id=data["message_id"],
        metadata=data.get("metadata") or {},
    )


class ConversationPersistence:
    """对话历史的磁盘持久化管理。

    将 ConversationHistory 序列化为 JSON 文件保存在指定目录下，
    文件名格式: {session_id}.json。
    """

    def __init__(self, storage_dir: str = "data/conversations"):
        self._storage_dir = Path(storage_dir)

    def save(self, history: ConversationHistory) -> str:
        """将对话历史保存为 JSON 文件，返回文件路径。"""
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "session_id": history.session_id,
            "created_at": history.created_at,
            "updated_at": history.updated_at,
            "turn_count": history.turn_count,
            "messages": [_serialize_message(m) for m in history.messages],
        }

        file_path = self._storage_dir / f"{history.session_id}.json"
        tmp_path = self._storage_dir / f"{history.session_id}.tmp"

        json_text = sanitize_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp_path.write_text(json_text, encoding="utf-8")
        os.replace(tmp_path, file_path)

        return str(file_path.resolve())

    def load(self, session_id: str) -> ConversationHistory | None:
        """从 JSON 文件加载指定会话的对话历史。"""
        file_path = self._storage_dir / f"{session_id}.json"
        if not file_path.exists():
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        messages = [_deserialize_message(m) for m in data.get("messages", [])]
        history = ConversationHistory(
            session_id=data["session_id"],
            messages=messages,
            turn_count=data.get("turn_count", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
        return history

    def storage_dir(self) -> str:
        return str(self._storage_dir.resolve())
