"""调试脚本 - 直接运行Agent进行测试

用法:
    py debug.py                    # 默认：三层编排图，日志级别 llm_io
    py debug.py --simple           # 简单图（快速模式）
    py debug.py --log-level full   # 最详细日志，包含工具调用
    py debug.py --log-level phases # 仅显示阶段切换
    py debug.py --log-level off    # 关闭日志
"""
import asyncio
import sys
import io
import argparse

sys.path.insert(0, 'src')

# 修复 Windows 控制台编码问题：强制 stdout 使用 UTF-8
sys.stdout = io.TextIOWrapper(
    sys.stdout.buffer,
    encoding='utf-8',
    errors='replace',
    line_buffering=True,
)


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
    from langchain_core.messages import HumanMessage

    # 初始化可观测性追踪器
    tracer = OrchestrationTracer()
    tracer.install()

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
            result = await graph.ainvoke({
                "messages": [HumanMessage(content=user_input)],
            })
            last_message = result["messages"][-1]
            print(f"\n{'─' * 60}")
            print(f"Agent: {last_message.content}")
            print(f"{'─' * 60}\n")
        except Exception as e:
            print(f"[错误] {type(e).__name__}: {e}\n")


async def main():
    parser = argparse.ArgumentParser(description="GraphAgent 调试脚本")
    parser.add_argument(
        "--simple", action="store_true",
        help="使用简单图（agent ↔ tools），响应更快",
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

    if args.simple:
        await run_simple()
    else:
        await run_orchestration()


if __name__ == "__main__":
    asyncio.run(main())
