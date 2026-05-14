"""Minimal LangChain agent graph for deployment."""

from __future__ import annotations

from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from graph_agent.llm import LLMFactory
from graph_agent.tools import ToolCenter

# 初始化工具中心并自动发现所有工具
tool_center = ToolCenter()
tool_center.auto_discover()
tools = tool_center.get_langchain_tools()

# 从环境变量创建 LLM 实例（一行代码，自动处理所有提供商差异）
llm_provider = LLMFactory.create_from_env()
llm = llm_provider.get_chat_model()
llm_with_tools = llm.bind_tools(tools)


def agent_node(state: MessagesState) -> dict:
    """Agent 推理节点：调用 LLM 决定下一步行动"""
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}


builder = StateGraph(MessagesState)

builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode(tools))

# 添加边
builder.add_edge(START, "agent")

# 条件路由：如果 LLM 请求工具则执行工具，否则结束
builder.add_conditional_edges(
    "agent",
    tools_condition,
    {
        "tools": "tools",
        END: END
    }
)

# 工具执行完后返回 agent 继续推理
builder.add_edge("tools", "agent")

graph = builder.compile()
