"""调试脚本 - 直接运行Agent进行测试

用法:
    py debug.py                    # 默认：三层编排图（直接模式），日志级别 llm_io
    py debug.py --simple           # 简单图（快速模式）
    py debug.py --acp              # 启动 ACP HTTP+SSE 服务（默认端口 8080）
    py debug.py --acp --port 9090  # 启动 ACP 服务并指定端口
    py debug.py --log-level full   # 最详细日志，包含工具调用
    py debug.py --log-level phases # 仅显示阶段切换
    py debug.py --log-level off    # 关闭日志
"""
import asyncio
import sys
import argparse

sys.path.insert(0, 'src')

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


async def run_simple():
    """简单图模式：agent ↔ tools，1-2 次 LLM 调用即可完成。"""
    from graph_agent.graph import graph
    from graph_agent.message import create_user_message

    print("=== Graph Agent 简单模式 ===")
    print("输入 'quit' 或 'exit' 退出\n")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            break

        if user_input.lower() in ['quit', 'exit', 'q']:
            print("再见!")
            break

        if not user_input:
            continue

        print("\nAgent 思考中...\n")

        try:
            user_ga = create_user_message(user_input)
            result = await graph.ainvoke({"ga_messages": [user_ga]})
            last_message = result["messages"][-1]
            print(f"Agent: {last_message.content}\n")
        except Exception as e:
            print(f"[错误] {type(e).__name__}: {e}\n")


async def run_orchestration():
    """三层编排图模式：GuardAgent → PlanAgent → SubAgent 完整流程。"""
    from graph_agent.orchestration.graph import build_orchestration_graph
    from graph_agent.tracer import OrchestrationTracer
    from graph_agent.session.history import ConversationHistory
    from graph_agent.session.persistence import ConversationPersistence
    from graph_agent.session.compressor import (
        SessionConfig, PriorityCompressor, SummaryCompressor,
    )
    from graph_agent.llm import LLMFactory
    from graph_agent.message.convert import agent_messages_to_langchain
    from langchain_core.messages import HumanMessage

    # 初始化可观测性追踪器
    tracer = OrchestrationTracer()
    tracer.install()

    # ── 多轮对话组件 ──────────────────────────────────────────
    session_config = SessionConfig.from_yaml("config/session_config.yaml")
    history = ConversationHistory()
    persistence = ConversationPersistence(session_config.storage_dir)
    priority_compressor = PriorityCompressor(session_config)
    summary_compressor = SummaryCompressor(session_config)
    llm_provider = LLMFactory.create_from_env()
    print(f"对话历史保存目录: {persistence.storage_dir()}")
    print(f"上下文窗口: {session_config.context_window} tokens")
    print(f"普通压缩阈值: {session_config.normal_threshold_tokens} tokens "
          f"({session_config.normal_compression_threshold*100:.0f}%)")
    print(f"高度压缩阈值: {session_config.aggressive_threshold_tokens} tokens "
          f"({session_config.aggressive_compression_threshold*100:.0f}%)")

    print("=== Graph Agent 编排模式 (三层架构) ===")
    print("输入 'quit' 或 'exit' 退出\n")

    graph = build_orchestration_graph()

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            break

        if user_input.lower() in ['quit', 'exit', 'q']:
            print("再见!")
            break

        if not user_input:
            continue

        print("\nAgent 思考中...\n")

        try:
            # ── 注入压缩后的历史上下文 ─────────────────────────
            token_count = history.estimate_tokens()

            # 普通压缩
            if token_count > session_config.normal_threshold_tokens:
                old_count = len(history.messages)
                compressed = priority_compressor.compress(history)
                history.replace_messages(compressed)
                new_count = len(history.messages)
                if new_count < old_count:
                    print(f"[普通压缩] {old_count} → {new_count} 条消息 "
                          f"({token_count} → {history.estimate_tokens()} tokens)")

            # 高度压缩（普通压缩后仍超阈值）
            token_count = history.estimate_tokens()
            if token_count > session_config.aggressive_threshold_tokens:
                old_count = len(history.messages)
                compressed = summary_compressor.compress(history, llm_provider)
                history.replace_messages(compressed)
                new_count = len(history.messages)
                print(f"[高度压缩] {old_count} → {new_count} 条消息 "
                      f"({token_count} → {history.estimate_tokens()} tokens)")

            # 过滤 + 注入：仅将语义有价值的消息注入 LLM 上下文
            context_msgs = history.get_context_messages()
            history_lc = agent_messages_to_langchain(context_msgs)
            context_messages = history_lc + [HumanMessage(content=user_input)]

            result = await graph.ainvoke({
                "messages": context_messages,
            })

            # ── 提取本轮 Agent 消息并追加到历史 ────────────────
            history.add_user_message(user_input)

            # 从 ga_messages 中收集本轮产生的 Agent 消息
            ga_msgs = result.get("ga_messages", [])
            if ga_msgs:
                history.add_agent_messages(list(ga_msgs))

            # ── 最终回复 ──────────────────────────────────────
            final_answer = result.get("final_answer", "")
            if not final_answer:
                final_answer = result["messages"][-1].content
            print(f"\n{'─' * 60}")
            print(f"Agent: {final_answer}")
            print(f"{'─' * 60}")

            # 将最终回复以 P1 优先级存入历史
            history.add_final_answer(final_answer)

            # ── 持久化 ───────────────────────────────────────
            if session_config.auto_save:
                save_path = persistence.save(history)
                print(f"[已保存] {save_path}\n")
            else:
                print()

        except Exception as e:
            import traceback
            print(f"[错误] {type(e).__name__}: {e}")
            traceback.print_exc()
            print()


async def run_acp_server(port: int = 8080):
    """启动 ACP HTTP+SSE 服务。"""
    print("正在初始化 GraphAgent ACP 服务...", flush=True)
    print("正在初始化 GraphAgent ACP 服务...", file=sys.stderr)

    try:
        from graph_agent.acp import ACPServer, HTTPSSETransport, ACPConfig
        from graph_agent.tracer import OrchestrationTracer
    except Exception as e:
        print(f"[错误] 模块导入失败: {e}", flush=True)
        print(f"[错误] 模块导入失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return

    print("  - 模块导入完成", flush=True)
    print("  - 模块导入完成", file=sys.stderr)

    # 初始化 Tracer
    tracer = OrchestrationTracer()
    tracer.install()

    config = ACPConfig.from_yaml()
    config.port = port
    config.host = "127.0.0.1"

    print("  - 正在构建编排图...", flush=True)
    try:
        server = ACPServer(config)
    except Exception as e:
        print(f"[错误] 服务初始化失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return

    print(f"\n=== GraphAgent ACP Server ===")
    print(f"监听地址: http://{config.host}:{config.port}")
    print(f"POST /acp/message  — 发送请求")
    print(f"GET  /acp/events   — SSE 事件流")
    print(f"GET  /health       — 健康检查")
    print(f"GET  /             — Web UI")
    print(f"会话存储: {server.session_manager.storage_dir}")
    print(f"最大并发会话: {config.max_sessions}")
    print(f"按 Ctrl+C 停止服务\n")

    transport = HTTPSSETransport(server, config)
    try:
        await transport.start()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        await transport.stop()


async def main():
    parser = argparse.ArgumentParser(description="GraphAgent 调试脚本")
    parser.add_argument(
        "--simple", action="store_true",
        help="使用简单图（agent ↔ tools），响应更快",
    )
    parser.add_argument(
        "--acp", action="store_true",
        help="启动 ACP HTTP+SSE 服务，供 Web UI 等客户端连接",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="ACP 服务端口 (默认: 8080，仅与 --acp 配合使用)",
    )
    parser.add_argument(
        "--log-level",
        choices=["off", "phases", "llm_io", "full"],
        default=None,
        help="可观测性日志级别 (默认: llm_io，也可通过 GRAPHAGENT_LOG_LEVEL 环境变量设置)",
    )
    args = parser.parse_args()

    # 如果命令行指定了 --log-level，设置到环境变量供 tracer 读取
    if args.log_level is not None:
        import os
        os.environ["GRAPHAGENT_LOG_LEVEL"] = args.log_level

    if args.acp:
        await run_acp_server(args.port)
    elif args.simple:
        await run_simple()
    else:
        await run_orchestration()


if __name__ == "__main__":
    asyncio.run(main())
