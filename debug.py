"""调试脚本 - 直接运行Agent进行测试"""
import asyncio
import sys

# 添加src到路径
sys.path.insert(0, 'src')

from langchain_core.messages import HumanMessage
from graph_agent.graph import graph


async def main():
    print("=== Graph Agent 调试模式 ===")
    print("输入 'quit' 或 'exit' 退出\n")

    while True:
        user_input = input("你: ").strip()
        if user_input.lower() in ['quit', 'exit', 'q']:
            print("再见!")
            break

        if not user_input:
            continue

        print("\nAgent 思考中...\n")

        # 运行Agent
        result = await graph.ainvoke({
            "messages": [HumanMessage(content=user_input)]
        })

        # 打印最终回复
        last_message = result["messages"][-1]
        print(f"Agent: {last_message.content}\n")


if __name__ == "__main__":
    asyncio.run(main())
