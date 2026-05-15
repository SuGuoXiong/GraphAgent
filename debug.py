"""调试脚本 - 直接运行Agent进行测试"""
import asyncio
import sys
import io

sys.path.insert(0, 'src')

# 修复 Windows 控制台编码问题：强制 stdout 使用 UTF-8
sys.stdout = io.TextIOWrapper(
    sys.stdout.buffer,
    encoding='utf-8',
    errors='replace',
    line_buffering=True,
)

from graph_agent.graph import graph
from graph_agent.message import create_user_message


async def main():
    print("=== Graph Agent 调试模式 ===")
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

            result = await graph.ainvoke({
                "ga_messages": [user_ga],
            })

            last_message = result["messages"][-1]
            print(f"Agent: {last_message.content}\n")
        except Exception as e:
            print(f"[错误] {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
