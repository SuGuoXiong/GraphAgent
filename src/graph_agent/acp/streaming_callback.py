"""ACP 流式回调 —— 将 LLM token 转换为 ACP 流式事件。

配合 streaming=True 使用，在同步 invoke() 调用期间由 LangChain
内部逐个 token 触发。每次 LLM 调用创建独立实例，天然线程安全。
"""

import logging
from langchain_core.callbacks import BaseCallbackHandler
from graph_agent.acp.protocol import ACPMessage, PushEvent

_logger = logging.getLogger(__name__)


class ACPStreamingCallback(BaseCallbackHandler):
    """LangChain 流式回调：将 on_llm_new_token 转换为 LLM_STREAM_CHUNK 事件。

    独家负责 LLM_STREAM_START、LLM_STREAM_CHUNK、LLM_STREAM_END 三个事件。
    _ACPTracerHook 在流式模式下跳过 LLM hook 注册以避免重复。
    """

    def __init__(self, push_fn, agent_name: str = ""):
        super().__init__()
        self._push = push_fn
        self._agent_name = agent_name
        self._token_count = 0

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._token_count = 0
        self._safe_push(ACPMessage.event(PushEvent.LLM_STREAM_START, {
            "agent_name": self._agent_name,
        }))

    def on_llm_new_token(self, token: str, **kwargs):
        self._token_count += 1
        self._safe_push(ACPMessage.event(PushEvent.LLM_STREAM_CHUNK, {
            "agent_name": self._agent_name,
            "token": token,
            "index": self._token_count,
        }))

    def on_llm_end(self, response, **kwargs):
        self._safe_push(ACPMessage.event(PushEvent.LLM_STREAM_END, {
            "agent_name": self._agent_name,
            "total_tokens": self._token_count,
        }))

    def on_llm_error(self, error, **kwargs):
        self._safe_push(ACPMessage.event(PushEvent.LLM_STREAM_END, {
            "agent_name": self._agent_name,
            "total_tokens": self._token_count,
            "error": str(error)[:200],
        }))

    def _safe_push(self, msg: ACPMessage) -> None:
        """异常保护的推送：回调中抛出异常可能被 LangChain 吞没。"""
        try:
            self._push(msg)
        except Exception:
            _logger.warning("ACPStreamingCallback: push failed", exc_info=True)
