"""ACPServer 核心 —— 传输无关的 Agent 服务。

职责:
1. 管理多个并发会话
2. 接收来自传输层的请求并分派到编排引擎
3. 将编排引擎内部事件转换为 ACP 事件推送给客户端
4. 会话生命周期管理（创建、加载、压缩、持久化）
5. 会话中断与恢复（检查点机制）
"""

from __future__ import annotations

import asyncio
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
    SessionStatus,
)
from graph_agent.acp.session_manager import SessionManager
from graph_agent.acp.checkpoint import (
    InterruptException,
    AskUserException,
    serialize_checkpoint,
    deserialize_checkpoint,
    generate_recovery_hint,
)
from graph_agent.message.convert import agent_messages_to_langchain
from graph_agent.orchestration.graph import build_orchestration_graph
from graph_agent.tracer.tracer import get_tracer, OrchestrationTracer


def _build_user_reply(ask_ctx: dict, reply: str, selected_option: int | None) -> str:
    """根据问题上下文和用户回答构建回复文本。"""
    if selected_option is not None and ask_ctx.get("options"):
        options = ask_ctx["options"]
        if 0 <= selected_option < len(options):
            return f"用户选择了: {options[selected_option]}"
    if ask_ctx.get("require_approval"):
        reply_lower = reply.lower()
        approved = reply_lower in ("yes", "是", "ok", "approve", "批准", "确认", "true", "1")
        return f"用户{'批准' if approved else '拒绝'}了该操作"
    return reply or "(用户未提供回答)"


def _inject_user_reply(state: dict, user_reply: str, tool_call_id: str = "") -> dict:
    """将用户回答作为 ask_user 的 ToolResult 注入到状态中。

    优先使用显式传入的 tool_call_id；未传入时回退到搜索 messages 中
    最后一个 ask_user 的 AIMessage。
    """
    from graph_agent.message import (
        ContentBlock, ToolResultBlock, MessageBlock, generate_message_id,
    )
    from graph_agent.message.message_type import MessageType
    from langchain_core.messages import ToolMessage, AIMessage

    messages = state.get("messages", [])
    ga_msgs = state.get("ga_messages", [])

    # 优先使用显式传入的 tool_call_id，否则搜索 messages
    ask_user_tool_id = tool_call_id
    if not ask_user_tool_id:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if tc_name == "ask_user":
                        ask_user_tool_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
                        break
                if ask_user_tool_id:
                    break

    if ask_user_tool_id:
        # 追加 ToolMessage 到 messages
        messages.append(ToolMessage(
            content=user_reply,
            tool_call_id=ask_user_tool_id,
            name="ask_user",
        ))
        state["messages"] = messages

        # 追加 ToolResult 到 ga_messages
        result_block = ContentBlock(
            block_type="tool_result",
            tool_result=ToolResultBlock(
                tool_id=ask_user_tool_id,
                tool_name="ask_user",
                output=user_reply,
                status="success",
            ),
        )
        ga_msgs.append(MessageBlock(
            role="tool",
            content=[result_block],
            name="ask_user",
            message_type=MessageType.TOOL_RESULT.value,
            message_id=generate_message_id(),
        ))
        state["ga_messages"] = ga_msgs

    return state


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
    """将 OrchestrationTracer 的输出同时路由到 ACP 事件收集器。

    通过 monkey-patch trace_phase/trace_decision + 注册临时 Type 3 Hook，
    实现编排/LLM/工具事件的 ACP 推送。
    """

    def __init__(self, collector: ACPEventCollector, tracer: OrchestrationTracer):
        self._collector = collector
        self._tracer = tracer
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

        # 通过 Hook 机制收集 LLM/工具事件（Type 3 会话级 Hook）
        from graph_agent.hook import HookContext, HookType, get_hook_executor

        executor = get_hook_executor()

        def _collect_tool_call(ctx: HookContext) -> None:
            collector.collect_tool_call(
                ctx.tool_name or "unknown",
                ctx.tool_args or {},
            )

        def _collect_tool_result(ctx: HookContext) -> None:
            collector.collect_tool_result(
                ctx.tool_name or "unknown",
                str(ctx.tool_result or "")[:500],
            )

        def _collect_llm_start(ctx: HookContext) -> None:
            preview = ""
            msgs = ctx.llm_messages or []
            if msgs:
                first = msgs[0]
                if hasattr(first, "content"):
                    preview = str(first.content)[:300]
                else:
                    preview = str(first)[:300]
            collector.collect_llm_start(ctx.llm_caller or "", preview)

        def _collect_llm_end(ctx: HookContext) -> None:
            collector.collect_llm_end(
                ctx.llm_caller or "",
                ctx.llm_token_usage,
            )

        # 注册为会话级 Hook（优先级 600，在 tracer 终端输出之后）
        register = executor.register
        register.add_session_hook(_collect_tool_call, "before_tool_call", 600, HookType.OBSERVE)
        register.add_session_hook(_collect_tool_result, "after_tool_call", 600, HookType.OBSERVE)
        register.add_session_hook(_collect_llm_start, "before_llm_call", 600, HookType.OBSERVE)
        register.add_session_hook(_collect_llm_end, "after_llm_call", 600, HookType.OBSERVE)

        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        self._tracer.trace_phase = self._original_trace_phase  # type: ignore[method-assign]
        self._tracer.trace_decision = self._original_trace_decision  # type: ignore[method-assign]

        from graph_agent.hook import get_hook_executor
        get_hook_executor().register.clear_session_hooks()

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
            RequestEvent.RESUME_SESSION: self._handle_resume_session,
            RequestEvent.REPLY_USER: self._handle_reply_user,
            RequestEvent.GET_SESSION_STATUS: self._handle_get_session_status,
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

    async def execute_turn(self, session_id: str, user_input: str = "") -> list[ACPMessage]:
        """执行一轮对话，返回 ACP 事件列表。

        支持三种模式：
        - 正常执行：user_input 非空，无 checkpoint
        - 恢复执行：user_input 为空，存在 checkpoint
        - 重新开始：user_input 非空，存在旧 checkpoint（放弃后重新开始）
        """
        events: list[ACPMessage] = []

        ctx = self._session_manager.get_context(session_id)
        if ctx is None:
            events.append(ACPMessage.error(
                ErrorCode.SESSION_NOT_FOUND,
                f"会话 '{session_id}' 不在活跃列表中，请先 load_session",
            ))
            return events

        if user_input and len(user_input) > 10000:
            events.append(ACPMessage.error(
                ErrorCode.INVALID_REQUEST,
                "消息内容超过 10000 字符限制",
            ))
            return events

        # 状态检查
        if ctx.status == SessionStatus.RUNNING.value:
            events.append(ACPMessage.error(
                ErrorCode.SESSION_BUSY,
                "会话正在执行中，无法重复操作",
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
        is_resume = False
        if ctx.checkpoint and not user_input:
            # 恢复模式：从检查点恢复
            is_resume = True
            ctx.status = SessionStatus.RESUMING.value
            initial_state = deserialize_checkpoint(ctx.checkpoint)

            # 如果是从 ask_user 中断恢复，注入用户回答
            if ctx.checkpoint.get("reason") == "ask_user" and "user_reply" in ctx.checkpoint:
                user_reply = ctx.checkpoint.pop("user_reply")
                ask_ctx = ctx.checkpoint.get("ask_user_context", {})
                ask_llm = initial_state.pop("_ask_user_llm_response", None)
                if ask_llm is not None:
                    initial_state["messages"].append(ask_llm)
                initial_state = _inject_user_reply(
                    initial_state, user_reply,
                    tool_call_id=ask_ctx.get("tool_call_id", ""),
                )
                # 将最后两条消息（AIMessage + ToolMessage）注入到 SubAgent 的 ReAct 循环中
                initial_state["_injected_messages"] = list(
                    initial_state.get("messages", [])[-2:]
                )

            events.append(ACPMessage.event(PushEvent.PHASE_CHANGED, {
                "phase": "resume",
                "detail": f"从检查点恢复，阶段: {initial_state.get('phase', '')}",
            }))
            events.append(ACPMessage.event(PushEvent.EXECUTION_RESUMED, {
                "session_id": session_id,
                "phase": str(initial_state.get("phase", "")),
                "recovery_hint": ctx.checkpoint.get("recovery_hint", ""),
            }))
        else:
            # 正常模式（或放弃旧 checkpoint 重新开始）
            if ctx.checkpoint:
                ctx.checkpoint = None  # 放弃旧检查点
            ctx.status = SessionStatus.RUNNING.value
            ctx.history.add_user_message(user_input)
            context_msgs = ctx.history.get_context_messages()
            context_lc = agent_messages_to_langchain(context_msgs)
            context_lc.append(HumanMessage(content=user_input))
            initial_state = {"messages": context_lc}

        # 3. 注入中断控制
        ctx.interrupt_event.clear()
        initial_state["_interrupt_event"] = ctx.interrupt_event

        # 4. 安装事件收集 hook
        collector = ACPEventCollector(session_id)
        hook = _ACPTracerHook(collector, self._tracer)
        hook.install()

        try:
            # 5. 执行编排图（带超时和中断捕获）
            try:
                timeout = self._config.execution_timeout
                result = await asyncio.wait_for(
                    self._graph.ainvoke(initial_state),
                    timeout=timeout,
                )
            except InterruptException as e:
                # 用户主动中断：保存检查点
                checkpoint = serialize_checkpoint(e.state, session_id, "interrupt")
                ctx.checkpoint = checkpoint
                ctx.status = SessionStatus.PAUSED.value
                self._session_manager.save_session(session_id)

                events.extend(collector.drain())
                plan = checkpoint.get("task_plan")
                sub_results = checkpoint.get("sub_results", {})
                events.append(ACPMessage.event(PushEvent.EXECUTION_PAUSED, {
                    "session_id": session_id,
                    "phase": checkpoint["phase"],
                    "recovery_hint": checkpoint.get("recovery_hint", ""),
                    "checkpoint_summary": {
                        "total_tasks": len(plan.get("sub_tasks", [])) if plan else 0,
                        "completed_tasks": len(sub_results),
                        "created_at": checkpoint.get("created_at", ""),
                    },
                }))
                return events

            except AskUserException as e:
                # Agent 向用户提问：保存检查点，推送 ASK_USER 事件
                state = e.state or {}
                checkpoint = serialize_checkpoint(state, session_id, "ask_user")
                checkpoint["ask_user_context"] = {
                    "question": e.question,
                    "options": e.options,
                    "require_approval": e.require_approval,
                    "tool_call_id": state.get("_ask_user_tool_id", ""),
                }
                ctx.checkpoint = checkpoint
                ctx.status = SessionStatus.AWAITING_USER.value
                self._session_manager.save_session(session_id)

                events.extend(collector.drain())
                events.append(ACPMessage.event(PushEvent.ASK_USER, {
                    "session_id": session_id,
                    "question": e.question,
                    "options": e.options,
                    "require_approval": e.require_approval,
                    "checkpoint_phase": checkpoint.get("phase", ""),
                }))
                return events

            except asyncio.TimeoutError:
                # 超时：尝试保存检查点后暂停
                events.extend(collector.drain())
                if ctx.status == SessionStatus.RUNNING.value or ctx.status == SessionStatus.RESUMING.value:
                    ctx.status = SessionStatus.PAUSED.value
                events.append(ACPMessage.event(PushEvent.EXECUTION_PAUSED, {
                    "session_id": session_id,
                    "reason": "timeout",
                    "recovery_hint": "执行超时，已自动保存检查点",
                }))
                return events

            # 6. 正常完成 — 收集阶段 + LLM + 工具事件
            events.extend(collector.drain())

            # 7. 追加 agent 消息到历史
            ga_msgs = result.get("ga_messages", [])
            if ga_msgs:
                ctx.history.add_agent_messages(list(ga_msgs))

            # 8. 提取最终回复
            final_answer = result.get("final_answer", "")
            if not final_answer:
                all_messages = result.get("messages", [])
                if all_messages:
                    final_msg = all_messages[-1]
                    final_answer = final_msg.content if hasattr(final_msg, 'content') else str(final_msg)
            if final_answer:
                ctx.history.add_final_answer(final_answer)

            events.append(ACPMessage.event(PushEvent.FINAL_ANSWER, {
                "content": final_answer,
            }))

            # 9. 清理检查点，标记完成
            ctx.checkpoint = None
            ctx.status = SessionStatus.COMPLETED.value
            self._session_manager.save_session(session_id)

            events.append(ACPMessage.event(PushEvent.EXECUTION_COMPLETE, {
                "session_id": session_id,
                "turn_count": ctx.history.turn_count,
            }))

        except InterruptException as e:
            # 在 hook 安装/卸载期间也可能捕获到延迟的中断
            checkpoint = serialize_checkpoint(e.state, session_id, "interrupt")
            ctx.checkpoint = checkpoint
            ctx.status = SessionStatus.PAUSED.value
            self._session_manager.save_session(session_id)

            events.extend(collector.drain())
            events.append(ACPMessage.event(PushEvent.EXECUTION_PAUSED, {
                "session_id": session_id,
                "phase": checkpoint["phase"],
                "recovery_hint": checkpoint.get("recovery_hint", ""),
            }))
            return events

        except Exception as e:
            import traceback
            ctx.status = SessionStatus.IDLE.value
            ctx.checkpoint = None
            events.append(ACPMessage.error(
                ErrorCode.EXECUTION_ERROR,
                str(e),
                detail={"traceback": traceback.format_exc()},
            ))
        finally:
            hook.uninstall()
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
        """设置中断信号，编排图将在下一个安全边界暂停。"""
        session_id = request.payload.get("session_id", "")
        if not session_id:
            return ACPMessage.error(ErrorCode.INVALID_REQUEST, "缺少 session_id", request_id=request.id)

        ctx = self._session_manager.get_context(session_id)
        if ctx is None:
            return ACPMessage.error(ErrorCode.SESSION_NOT_FOUND, f"会话不存在: {session_id}", request_id=request.id)

        if ctx.status != SessionStatus.RUNNING.value and ctx.status != SessionStatus.RESUMING.value:
            return ACPMessage.error(
                ErrorCode.INVALID_REQUEST,
                f"会话状态为 {ctx.status}，无法中断",
                request_id=request.id,
            )

        ctx.interrupt_event.set()
        return ACPMessage.response(ResponseEvent.ACK, {
            "request_id": request.id,
            "session_id": session_id,
            "status": "interrupt_requested",
            "message": "中断信号已发送，等待编排图到达安全边界",
        }, request_id=request.id)

    def _handle_reply_user(self, request: ACPMessage) -> ACPMessage:
        """处理用户对 ask_user 的回答。"""
        session_id = request.payload.get("session_id", "")
        reply = request.payload.get("reply", "")
        selected_option = request.payload.get("selected_option")

        if not session_id:
            return ACPMessage.error(ErrorCode.INVALID_REQUEST, "缺少 session_id",
                                    request_id=request.id)

        ctx = self._session_manager.get_context(session_id)
        if ctx is None:
            return ACPMessage.error(ErrorCode.SESSION_NOT_FOUND,
                                    f"会话 '{session_id}' 不存在", request_id=request.id)

        if ctx.status != SessionStatus.AWAITING_USER.value:
            return ACPMessage.error(ErrorCode.SESSION_NOT_PAUSED,
                                    f"会话状态为 {ctx.status}，未在等待用户回复",
                                    request_id=request.id)

        if not ctx.checkpoint or "ask_user_context" not in ctx.checkpoint:
            return ACPMessage.error(ErrorCode.CHECKPOINT_INVALID,
                                    "检查点中缺少 ask_user 上下文",
                                    request_id=request.id)

        ask_ctx = ctx.checkpoint["ask_user_context"]
        response_text = _build_user_reply(ask_ctx, reply, selected_option)

        ctx.checkpoint["user_reply"] = response_text
        self._session_manager.save_session(session_id)

        return ACPMessage.response(ResponseEvent.ACK, {
            "reply_accepted": True,
            "session_id": session_id,
        }, request_id=request.id)

    async def resume_session(self, session_id: str) -> list[ACPMessage]:
        """从检查点恢复会话执行（支持 PAUSED 和 AWAITING_USER 状态）。"""
        ctx = self._session_manager.get_context(session_id)
        if ctx is None:
            return [ACPMessage.error(ErrorCode.SESSION_NOT_FOUND, f"会话 '{session_id}' 不存在")]

        if ctx.status not in (SessionStatus.PAUSED.value, SessionStatus.AWAITING_USER.value):
            return [ACPMessage.error(ErrorCode.SESSION_NOT_PAUSED, f"会话状态为 {ctx.status}，无法恢复")]

        if not ctx.checkpoint:
            return [ACPMessage.error(ErrorCode.CHECKPOINT_INVALID, "检查点数据不存在")]

        return await self.execute_turn(session_id, user_input="")

    def _handle_resume_session(self, request: ACPMessage) -> ACPMessage:
        """处理恢复请求（同步 ack，实际恢复由传输层异步执行）。"""
        session_id = request.payload.get("session_id", "")
        if not session_id:
            return ACPMessage.error(ErrorCode.INVALID_REQUEST, "缺少 session_id", request_id=request.id)

        ctx = self._session_manager.get_context(session_id)
        if ctx is None:
            return ACPMessage.error(ErrorCode.SESSION_NOT_FOUND, f"会话不存在: {session_id}", request_id=request.id)

        if ctx.status not in (SessionStatus.PAUSED.value, SessionStatus.AWAITING_USER.value):
            return ACPMessage.error(
                ErrorCode.SESSION_NOT_PAUSED,
                f"会话状态为 {ctx.status}，无法恢复",
                request_id=request.id,
            )

        if not ctx.checkpoint:
            return ACPMessage.error(ErrorCode.CHECKPOINT_INVALID, "检查点数据不存在", request_id=request.id)

        return ACPMessage.response(ResponseEvent.ACK, {
            "request_id": request.id,
            "session_id": session_id,
            "status": "resume_accepted",
            "checkpoint_summary": {
                "phase": ctx.checkpoint.get("phase", ""),
                "recovery_hint": ctx.checkpoint.get("recovery_hint", ""),
            },
        }, request_id=request.id)

    def _handle_get_session_status(self, request: ACPMessage) -> ACPMessage:
        """返回会话的完整状态信息。"""
        session_id = request.payload.get("session_id", "")
        if not session_id:
            return ACPMessage.error(ErrorCode.INVALID_REQUEST, "缺少 session_id", request_id=request.id)

        status_info = self._session_manager.get_session_status(session_id)
        if status_info is None:
            return ACPMessage.error(ErrorCode.SESSION_NOT_FOUND, f"会话 '{session_id}' 不存在", request_id=request.id)

        return ACPMessage.response(ResponseEvent.ACK, status_info, request_id=request.id)
