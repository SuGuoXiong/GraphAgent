"""ACPServer 核心 —— 传输无关的 Agent 服务。

职责:
1. 管理多个并发会话
2. 接收来自传输层的请求并分派到编排引擎
3. 将编排引擎内部事件转换为 ACP 事件推送给客户端
4. 会话生命周期管理（创建、加载、压缩、持久化）
"""

from __future__ import annotations

import queue
from typing import Any

from langchain_core.messages import HumanMessage

from graph_agent.acp.protocol import (
    ACPMessage,
    ACPConfig,
    CompressionResult,
    PushEvent,
    ResponseEvent,
    RequestEvent,
    ErrorCode,
)
from graph_agent.acp.session_manager import SessionManager
from graph_agent.message.convert import agent_messages_to_langchain
from graph_agent.orchestration.graph import build_orchestration_graph
from graph_agent.tracer.tracer import get_tracer, OrchestrationTracer


class ACPEventCollector:
    """线程安全的事件收集器 —— 拦截编排/LLM/工具事件。

    在 graph.ainvoke() 执行期间，通过 hook tracer callback
    将内部可观测事件收集到此队列中，执行完毕后统一取出。
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._queue: queue.Queue[ACPMessage] = queue.Queue()
        self._phase_count = 0
        self._tool_count = 0

    def push(self, msg: ACPMessage) -> None:
        self._queue.put(msg)

    def drain(self) -> list[ACPMessage]:
        events: list[ACPMessage] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def collect_phase(self, phase_name: str, agent_name: str, detail: str = "") -> None:
        self._phase_count += 1
        self.push(ACPMessage.event(PushEvent.PHASE_CHANGED, {
            "phase": phase_name,
            "agent_name": agent_name,
            "detail": detail,
        }))

    def collect_decision(self, agent_name: str, decision: str, reason: str = "") -> None:
        self.push(ACPMessage.event(PushEvent.PHASE_CHANGED, {
            "phase": "decision",
            "agent_name": agent_name,
            "detail": decision,
            "reason": reason,
        }))

    def collect_tool_call(self, tool_name: str, input_args: dict) -> None:
        self._tool_count += 1
        self.push(ACPMessage.event(PushEvent.TOOL_CALL, {
            "tool_name": tool_name,
            "input_args": {k: str(v)[:200] for k, v in input_args.items()},
        }))

    def collect_tool_result(self, tool_name: str, output: str) -> None:
        self.push(ACPMessage.event(PushEvent.TOOL_RESULT, {
            "tool_name": tool_name,
            "output_preview": output[:500],
        }))

    def collect_llm_start(self, agent_name: str, prompt_preview: str) -> None:
        self.push(ACPMessage.event(PushEvent.LLM_STREAM_START, {
            "agent_name": agent_name,
            "prompt_preview": prompt_preview[:300],
        }))

    def collect_llm_end(self, agent_name: str, token_usage: dict | None = None) -> None:
        self.push(ACPMessage.event(PushEvent.LLM_STREAM_END, {
            "agent_name": agent_name,
            "token_usage": token_usage or {},
        }))


class _ACPTracerHook:
    """将 OrchestrationTracer 的输出同时路由到原生终端的 collector。

    不替换 tracer 实例本身，而是替换其 _llm_callback 为
    一个同时写入原始 handler 和 collector 的装饰器。
    """

    def __init__(self, collector: ACPEventCollector, tracer: OrchestrationTracer):
        self._collector = collector
        self._tracer = tracer
        self._original_callback = tracer.get_llm_callback()
        self._original_trace_phase = tracer.trace_phase
        self._original_trace_decision = tracer.trace_decision
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        tracer = self._tracer
        collector = self._collector

        # 钩入 trace_phase
        def _hooked_phase(phase_name: str, agent_name: str, detail: str = "") -> None:
            collector.collect_phase(phase_name, agent_name, detail)
            self._original_trace_phase(phase_name, agent_name, detail)

        tracer.trace_phase = _hooked_phase  # type: ignore[method-assign]

        # 钩入 trace_decision
        def _hooked_decision(agent_name: str, decision: str, reason: str = "") -> None:
            collector.collect_decision(agent_name, decision, reason)
            self._original_trace_decision(agent_name, decision, reason)

        tracer.trace_decision = _hooked_decision  # type: ignore[method-assign]

        # 钩入 LLM callback
        if self._original_callback:
            original_on_tool_start = getattr(self._original_callback, 'on_tool_start', None)
            original_on_tool_end = getattr(self._original_callback, 'on_tool_end', None)
            original_on_llm_start = getattr(self._original_callback, 'on_llm_start', None)
            original_on_llm_end = getattr(self._original_callback, 'on_llm_end', None)

            class HookedCallback(type(self._original_callback)):
                def on_tool_start(self, serialized, input_str, **kwargs):
                    name = serialized.get("name", "") if isinstance(serialized, dict) else getattr(serialized, "name", "")
                    collector.collect_tool_call(name, {"input": str(input_str)[:200]})
                    if original_on_tool_start:
                        original_on_tool_start(serialized, input_str, **kwargs)

                def on_tool_end(self, output, **kwargs):
                    out = str(output)[:500] if output else ""
                    collector.collect_tool_result("", out)
                    if original_on_tool_end:
                        original_on_tool_end(output, **kwargs)

                def on_llm_start(self, serialized, prompts, **kwargs):
                    preview = ""
                    if prompts:
                        first = prompts[0] if isinstance(prompts, list) else str(prompts)
                        if hasattr(first, 'content'):
                            preview = str(first.content)[:300]
                        else:
                            preview = str(first)[:300]
                    collector.collect_llm_start("", preview)
                    if original_on_llm_start:
                        original_on_llm_start(serialized, prompts, **kwargs)

                def on_llm_end(self, response, **kwargs):
                    token_usage = {}
                    if hasattr(response, 'llm_output') and response.llm_output:
                        token_usage = response.llm_output.get("token_usage", {})
                    collector.collect_llm_end("", token_usage)
                    if original_on_llm_end:
                        original_on_llm_end(response, **kwargs)

            tracer._llm_callback = HookedCallback(log_level=tracer.log_level)

        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        self._tracer.trace_phase = self._original_trace_phase  # type: ignore[method-assign]
        self._tracer.trace_decision = self._original_trace_decision  # type: ignore[method-assign]
        self._tracer._llm_callback = self._original_callback
        self._installed = False


class ACPServer:
    """ACP 服务端 —— 传输无关的 Agent 服务。

    使用方式:
        server = ACPServer()
        server.bind_transport(http_transport)
        await server.start()
    """

    def __init__(self, config: ACPConfig | None = None):
        self._config = config or ACPConfig()
        self._session_manager = SessionManager(self._config)
        self._graph = build_orchestration_graph()
        self._tracer = get_tracer()
        self._request_handlers: dict[str, Any] = {}

    @property
    def session_manager(self) -> SessionManager:
        return self._session_manager

    @property
    def config(self) -> ACPConfig:
        return self._config

    # ── 请求分发 ──────────────────────────────────────────

    def handle_request(self, request: ACPMessage) -> ACPMessage:
        """处理客户端请求并返回同步回复（同步方法，所有 handler 均为同步）。"""
        req_event = request.get_request_event()
        if req_event is None:
            return ACPMessage.error(
                ErrorCode.INVALID_REQUEST,
                f"未知请求事件: {request.event}",
                request_id=request.id,
            )

        handler_map = {
            RequestEvent.CREATE_SESSION: self._handle_create_session,
            RequestEvent.LOAD_SESSION: self._handle_load_session,
            RequestEvent.LIST_SESSIONS: self._handle_list_sessions,
            RequestEvent.DELETE_SESSION: self._handle_delete_session,
            RequestEvent.CONFIGURE: self._handle_configure,
            RequestEvent.INTERRUPT: self._handle_interrupt,
        }

        handler = handler_map.get(req_event)
        if handler is None:
            return ACPMessage.ack(request.id, status="ignored")

        try:
            return handler(request)
        except Exception as e:
            return ACPMessage.error(
                ErrorCode.INTERNAL_ERROR,
                str(e),
                request_id=request.id,
            )

    async def execute_turn(self, session_id: str, user_input: str) -> list[ACPMessage]:
        """执行一轮对话，返回 ACP 事件列表。

        这是 send_message 请求的核心实现。
        """
        events: list[ACPMessage] = []

        ctx = self._session_manager.get_context(session_id)
        if ctx is None:
            events.append(ACPMessage.error(
                ErrorCode.SESSION_NOT_FOUND,
                f"会话 '{session_id}' 不在活跃列表中，请先 load_session",
            ))
            return events

        if len(user_input) > 10000:
            events.append(ACPMessage.error(
                ErrorCode.INVALID_REQUEST,
                "消息内容超过 10000 字符限制",
            ))
            return events

        # 1. 压缩检查
        comp_result = self._session_manager.compress_if_needed(session_id)
        if comp_result.did_compress:
            events.append(ACPMessage.event(PushEvent.PHASE_CHANGED, {
                "phase": "compression",
                "detail": (
                    f"压缩完成 ({comp_result.level}): "
                    f"{comp_result.before_count} → {comp_result.after_count} 条消息, "
                    f"{comp_result.before_tokens} → {comp_result.after_tokens} tokens"
                ),
            }))

        # 2. 准备上下文
        ctx.history.add_user_message(user_input)
        context_msgs = ctx.history.get_context_messages()
        context_lc = agent_messages_to_langchain(context_msgs)
        context_lc.append(HumanMessage(content=user_input))

        # 3. 安装事件收集 hook
        collector = ACPEventCollector(session_id)
        hook = _ACPTracerHook(collector, self._tracer)
        hook.install()

        # 4. 执行编排图
        try:
            result = await self._graph.ainvoke({"messages": context_lc})

            # 5. 收集阶段 + LLM + 工具事件
            events.extend(collector.drain())

            # 6. 追加 agent 消息到历史
            ga_msgs = result.get("ga_messages", [])
            if ga_msgs:
                ctx.history.add_agent_messages(list(ga_msgs))

            # 7. 提取最终回复
            final_answer = result.get("final_answer", "")
            if not final_answer:
                all_messages = result.get("messages", [])
                if all_messages:
                    final_msg = all_messages[-1]
                    final_answer = final_msg.content if hasattr(final_msg, 'content') else str(final_msg)
            ctx.history.add_final_answer(final_answer)

            events.append(ACPMessage.event(PushEvent.FINAL_ANSWER, {
                "content": final_answer,
            }))

            # 8. 持久化
            self._session_manager.save_session(session_id)

            events.append(ACPMessage.event(PushEvent.EXECUTION_COMPLETE, {
                "session_id": session_id,
                "turn_count": ctx.history.turn_count,
            }))

        except Exception as e:
            import traceback
            events.append(ACPMessage.error(
                ErrorCode.EXECUTION_ERROR,
                str(e),
                detail={"traceback": traceback.format_exc()},
            ))
        finally:
            hook.uninstall()
            # 确保 hook 期间未取完的事件也被收集
            events.extend(collector.drain())

        ctx.touch()
        return events

    # ── 内部处理器 ────────────────────────────────────────

    def _handle_create_session(self, request: ACPMessage) -> ACPMessage:
        overrides = request.payload.get("config", None)
        session_id = self._session_manager.create_session(overrides)
        return ACPMessage.response(ResponseEvent.SESSION_CREATED, {
            "session_id": session_id,
        }, request_id=request.id)

    def _handle_load_session(self, request: ACPMessage) -> ACPMessage:
        session_id = request.payload.get("session_id", "")
        if not session_id:
            return ACPMessage.error(
                ErrorCode.INVALID_REQUEST,
                "缺少 session_id 参数",
                request_id=request.id,
            )
        ctx = self._session_manager.load_session(session_id)
        if ctx is None:
            return ACPMessage.error(
                ErrorCode.SESSION_NOT_FOUND,
                f"会话 '{session_id}' 不存在",
                request_id=request.id,
            )
        return ACPMessage.response(ResponseEvent.SESSION_LOADED, {
            "session_id": session_id,
            "turn_count": ctx.history.turn_count,
            "message_count": len(ctx.history.messages),
        }, request_id=request.id)

    def _handle_list_sessions(self, request: ACPMessage) -> ACPMessage:
        sessions = self._session_manager.list_sessions()
        return ACPMessage.response(ResponseEvent.SESSION_LIST, {
            "sessions": [s.to_dict() for s in sessions],
        }, request_id=request.id)

    def _handle_delete_session(self, request: ACPMessage) -> ACPMessage:
        session_id = request.payload.get("session_id", "")
        if not session_id:
            return ACPMessage.error(
                ErrorCode.INVALID_REQUEST,
                "缺少 session_id 参数",
                request_id=request.id,
            )
        deleted = self._session_manager.delete_session(session_id)
        if not deleted:
            return ACPMessage.error(
                ErrorCode.SESSION_NOT_FOUND,
                f"会话 '{session_id}' 不存在",
                request_id=request.id,
            )
        return ACPMessage.response(ResponseEvent.SESSION_DELETED, {
            "session_id": session_id,
        }, request_id=request.id)

    def _handle_configure(self, request: ACPMessage) -> ACPMessage:
        return ACPMessage.ack(request.id, status="configured")

    def _handle_interrupt(self, request: ACPMessage) -> ACPMessage:
        return ACPMessage.ack(request.id, status="interrupted")
