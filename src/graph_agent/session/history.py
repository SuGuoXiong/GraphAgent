"""ConversationHistory —— 多轮对话历史管理。

封装 List[MessageBlock] 并提供添加、查询、Token 估算等操作。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from graph_agent.message import (
    MessageBlock,
    create_user_message,
    generate_message_id,
)
from graph_agent.message.message_type import MessageType
from graph_agent.session.context_filter import is_context_eligible
from graph_agent.session.token_counter import estimate_tokens


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


@dataclass
class ConversationHistory:
    """一次运行周期内的完整对话历史。

    所有 Agent 消息（GuardAgent / PlanAgent / SubAgent）与用户消息
    统一存储在同一列表中，按时间顺序追加。轮次边界由用户消息上的
    metadata["turn_index"] 标记。
    """

    session_id: str = field(default_factory=_session_id)
    messages: list[MessageBlock] = field(default_factory=list)
    turn_count: int = 0
    created_at: str = field(default_factory=_iso_now)
    updated_at: str = ""

    def add_user_message(self, content: str) -> MessageBlock:
        """添加用户消息，轮次 +1。"""
        self.turn_count += 1
        msg = create_user_message(
            content=content,
            message_type=MessageType.USER_INPUT,
            metadata={"turn_index": self.turn_count},
        )
        self.messages.append(msg)
        self._touch()
        return msg

    def add_agent_messages(self, messages: list[MessageBlock]) -> None:
        """批量添加 Agent 侧消息。"""
        for msg in messages:
            if msg.metadata is None:
                msg.metadata = {"turn_index": self.turn_count}
            else:
                msg.metadata.setdefault("turn_index", self.turn_count)
        self.messages.extend(messages)
        self._touch()

    def get_last_n_turns(self, n: int) -> list[MessageBlock]:
        """获取最近 n 轮对话的消息列表。

        从 messages 末尾向前扫描，收集 metadata["turn_index"]
        落在最后 n 个轮次范围内的消息。
        """
        if n <= 0 or not self.messages:
            return []
        current_turn = self.turn_count
        min_turn = max(1, current_turn - n + 1)
        result: list[MessageBlock] = []
        for msg in self.messages:
            turn_idx = msg.metadata.get("turn_index", 0) if msg.metadata else 0
            if turn_idx >= min_turn:
                result.append(msg)
        return result

    def estimate_tokens(self) -> int:
        """估算所有历史消息的总 Token 数。"""
        total = 0
        for msg in self.messages:
            content = msg.content
            if isinstance(content, str):
                total += estimate_tokens(content)
            elif isinstance(content, list):
                for block in content:
                    if block.block_type == "text" and block.text:
                        total += estimate_tokens(block.text)
                    elif block.block_type == "thinking" and block.thinking:
                        total += estimate_tokens(block.thinking)
        return total

    def replace_messages(self, messages: list[MessageBlock]) -> None:
        """用新列表替换全部消息（压缩后调用）。"""
        self.messages = messages
        self._touch()

    def add_final_answer(self, content: str) -> None:
        """添加最终回复消息（P1 优先级，供后续指代消解和追问）。"""
        if not content:
            return
        msg = MessageBlock(
            role="assistant",
            content=content,
            name="",
            message_type=MessageType.FINAL_ANSWER.value,
            message_id=generate_message_id(),
            metadata={"turn_index": self.turn_count},
        )
        self.messages.append(msg)
        self._touch()

    def get_context_messages(self) -> list[MessageBlock]:
        """获取应注入 LLM 上下文的消息列表（已过滤内部审核/派发/工具产物）。"""
        return [
            m for m in self.messages
            if is_context_eligible(MessageType(m.message_type))
        ]

    def _touch(self) -> None:
        self.updated_at = _iso_now()
