"""两级上下文压缩：普通压缩（优先级删除）+ 高度压缩（LLM 摘要）。"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from graph_agent.message import MessageBlock, create_assistant_message, generate_message_id
from graph_agent.message.message_type import MessageType, get_compression_priority
from graph_agent.session.history import ConversationHistory
from graph_agent.session.token_counter import estimate_tokens

if TYPE_CHECKING:
    from graph_agent.llm.base import LLMProvider


# ── SessionConfig ──────────────────────────────────────────────

@dataclass
class SessionConfig:
    """多轮对话与上下文窗口管理配置。"""
    context_window: int = 65536
    normal_compression_threshold: float = 0.6
    aggressive_compression_threshold: float = 0.8
    keep_recent_turns: int = 3
    storage_dir: str = "data/conversations"
    auto_save: bool = True
    token_estimation_method: str = "char_ratio"

    @property
    def normal_threshold_tokens(self) -> int:
        return int(self.context_window * self.normal_compression_threshold)

    @property
    def aggressive_threshold_tokens(self) -> int:
        return int(self.context_window * self.aggressive_compression_threshold)

    @classmethod
    def from_yaml(cls, path: str = "config/session_config.yaml") -> "SessionConfig":
        config_path = Path(path)
        if not config_path.exists():
            return cls()
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        session = data.get("session", {})
        persistence = session.get("persistence", {})
        te = session.get("token_estimation", {})
        return cls(
            context_window=session.get("context_window", 65536),
            normal_compression_threshold=session.get("normal_compression_threshold", 0.6),
            aggressive_compression_threshold=session.get("aggressive_compression_threshold", 0.8),
            keep_recent_turns=session.get("keep_recent_turns", 3),
            storage_dir=persistence.get("storage_dir", "data/conversations"),
            auto_save=persistence.get("auto_save", True),
            token_estimation_method=te.get("method", "char_ratio"),
        )


# ── PriorityCompressor ─────────────────────────────────────────

class PriorityCompressor:
    """基于 message_type 优先级的普通压缩器。

    从 P4 → P1 逐级删除低价值消息，直到 Token 用量降到
    normal_threshold_tokens 以下。保护窗口内的最近 N 轮对话不受影响。
    """

    def __init__(self, config: SessionConfig):
        self._config = config

    def compress(self, history: ConversationHistory) -> list[MessageBlock]:
        """执行普通压缩，返回压缩后的消息列表。"""
        total_tokens = history.estimate_tokens()
        if total_tokens <= self._config.normal_threshold_tokens:
            return list(history.messages)

        msgs = list(history.messages)
        kept, old = self._split_by_recent_window(msgs, history.turn_count)

        old_by_priority = self._group_by_priority(old)
        old_kept = list(old)
        current_tokens = self._sum_tokens(kept + old_kept)

        # 从 P4 → P1 逐组删除
        for priority in [4, 3, 2, 1]:
            if current_tokens <= self._config.normal_threshold_tokens:
                break
            candidates = old_by_priority.get(priority, [])
            if not candidates:
                continue
            # P3: 保留最近 2 轮；P2: 保留最近 3 轮
            reserve = 0
            if priority == 3:
                reserve = min(2, self._config.keep_recent_turns)
            elif priority == 2:
                reserve = min(3, self._config.keep_recent_turns)
            if reserve > 0:
                min_turn = max(1, history.turn_count - reserve + 1)
                reserved = [m for m in candidates
                            if (m.metadata or {}).get("turn_index", 0) >= min_turn]
                for m in reserved:
                    if m in old_kept:
                        old_kept.remove(m)
                candidates = [m for m in candidates if m in old_kept]

            for m in candidates:
                if current_tokens <= self._config.normal_threshold_tokens:
                    break
                if m in old_kept:
                    old_kept.remove(m)
                    current_tokens -= self._msg_tokens(m)

        return kept + old_kept

    def _split_by_recent_window(
        self, messages: list[MessageBlock], current_turn: int
    ) -> tuple[list[MessageBlock], list[MessageBlock]]:
        """分离保护窗口（最近 N 轮）和可压缩区。"""
        n = self._config.keep_recent_turns
        min_turn = max(1, current_turn - n + 1)
        recent: list[MessageBlock] = []
        old: list[MessageBlock] = []
        for m in messages:
            turn_idx = (m.metadata or {}).get("turn_index", 0)
            if turn_idx >= min_turn:
                recent.append(m)
            else:
                old.append(m)
        return recent, old

    @staticmethod
    def _group_by_priority(messages: list[MessageBlock]) -> dict[int, list[MessageBlock]]:
        groups: dict[int, list[MessageBlock]] = {}
        for m in messages:
            mt = MessageType(m.message_type) if m.message_type else None
            p = get_compression_priority(mt) if mt else 2
            groups.setdefault(p, []).append(m)
        return groups

    @staticmethod
    def _msg_tokens(msg: MessageBlock) -> int:
        if isinstance(msg.content, str):
            return estimate_tokens(msg.content)
        total = 0
        for b in msg.content:
            if b.text:
                total += estimate_tokens(b.text)
            if b.thinking:
                total += estimate_tokens(b.thinking)
        return total

    @staticmethod
    def _sum_tokens(messages: list[MessageBlock]) -> int:
        return sum(PriorityCompressor._msg_tokens(m) for m in messages)


# ── SummaryCompressor ──────────────────────────────────────────

_SUMMARY_PROMPT = """你是一个对话摘要助手。请将以下对话历史压缩为一段简洁的结构化摘要。

## 摘要要求
1. 保留每轮用户问题的核心意图
2. 保留关键的执行结果和数值
3. 忽略工具调用细节和中间推理过程
4. 按时间顺序组织

## 对话历史
{conversation_text}

## 输出格式
请以 JSON 格式输出:
{{
  "summary": "一段简洁的摘要文本",
  "turns_covered": "第 1 ~ N 轮",
  "key_results": ["结果1", "结果2"]
}}"""


class SummaryCompressor:
    """基于 LLM 摘要的高度压缩器。

    将保护窗口之前的全部消息压缩为一条 LLM 生成的摘要消息，
    大幅降低上下文窗口占用。
    """

    def __init__(self, config: SessionConfig):
        self._config = config

    def compress(
        self, history: ConversationHistory, provider: "LLMProvider",
    ) -> list[MessageBlock]:
        """执行高度压缩，返回压缩后的消息列表。"""
        n = self._config.keep_recent_turns
        min_turn = max(1, history.turn_count - n + 1)

        recent: list[MessageBlock] = []
        old: list[MessageBlock] = []
        for m in history.messages:
            turn_idx = (m.metadata or {}).get("turn_index", 0)
            if turn_idx >= min_turn:
                recent.append(m)
            else:
                old.append(m)

        if not old:
            return recent

        turns_covered = self._turns_range(old)
        conversation_text = self._messages_to_text(old)

        user_text = _SUMMARY_PROMPT.format(conversation_text=conversation_text)
        system_prompt = "你是一个专业的对话摘要助手。请严格按 JSON 格式输出。"
        try:
            summary_text = self._call_summary_llm(provider, system_prompt, user_text)
        except Exception:
            summary_text = f"对话历史摘要（第 {turns_covered} 轮）：\n{conversation_text[:500]}"

        summary_json = self._parse_json(summary_text)
        summary = summary_json.get("summary", summary_text)

        summary_msg = create_assistant_message(
            content=summary,
            message_type=MessageType.AGENT_RESPONSE,
            name="Compressor",
            metadata={
                "compression": "aggressive",
                "turns_covered": turns_covered,
                "original_message_count": len(old),
            },
        )
        return [summary_msg] + recent

    @staticmethod
    def _turns_range(messages: list[MessageBlock]) -> str:
        indices = sorted({
            (m.metadata or {}).get("turn_index", 0)
            for m in messages
            if (m.metadata or {}).get("turn_index", 0) > 0
        })
        if not indices:
            return "1 ~ 1"
        return f"{indices[0]} ~ {indices[-1]}"

    @staticmethod
    def _messages_to_text(messages: list[MessageBlock]) -> str:
        lines: list[str] = []
        for m in messages:
            content = m.content
            if isinstance(content, str):
                text = content
            else:
                parts = [b.text or b.thinking or "" for b in content]
                text = " ".join(p for p in parts if p)
            if text:
                role_label = f"[{m.name or m.role}]"
                lines.append(f"{role_label} {text[:300]}")
        return "\n".join(lines) if lines else "(无历史消息)"

    @staticmethod
    def _call_summary_llm(provider: "LLMProvider", system_prompt: str, user_text: str) -> str:
        from langchain_core.messages import SystemMessage, HumanMessage
        llm = provider.get_chat_model()
        response = llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_text)],
            config={"run_name": "SummaryCompressor"},
        )
        return response.content if hasattr(response, 'content') else str(response)

    @staticmethod
    def _parse_json(text: str) -> dict:
        import re
        cleaned = text.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', cleaned)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass
        start = cleaned.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(cleaned)):
                ch = cleaned[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start:i + 1])
                        except json.JSONDecodeError:
                            break
        return {}
