"""GraphAgent graph — 使用 MessagesState + ga_messages 双通道传递信息。"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from graph_agent.state import AgentState
from graph_agent.llm import LLMFactory
from graph_agent.tools import ToolCenter
from graph_agent.message import (
    agent_messages_to_langchain,
    langchain_to_agent_message,
    langchain_to_agent_messages,
)

# 初始化工具中心并自动发现所有工具
tool_center = ToolCenter()
tool_center.auto_discover()
tools = tool_center.get_langchain_tools()

# 从环境变量创建 LLM 实例
llm_provider = LLMFactory.create_from_env()
llm = llm_provider.get_chat_model()
llm_with_tools = llm.bind_tools(tools)


def _ensure_synced(state: AgentState) -> dict:
    """双向增量同步 ga_messages ↔ messages，将结果直接写入 state 并返回需要持久化的数据。

    只转换尾部未同步的条目，已同步的历史消息不做重复转换。
    返回的 sync 字典由节点合并到自己的返回值中，通过 reducer 持久化到 AgentState。
    """
    ga_messages = state.get("ga_messages", [])
    messages = state.get("messages", [])

    sync: dict = {}

    # ga_messages 领先 → 转换 MessageBlock 为 LangChain 消息
    gap_ga = len(ga_messages) - len(messages)
    if gap_ga > 0:
        lc_add = agent_messages_to_langchain(ga_messages[-gap_ga:])
        state["messages"] = list(messages) + lc_add
        sync["messages"] = lc_add

    # messages 领先 → 转换 LangChain 消息为 MessageBlock
    gap_lc = len(state["messages"]) - len(ga_messages)
    if gap_lc > 0:
        ga_add = langchain_to_agent_messages(state["messages"][-gap_lc:])
        state["ga_messages"] = list(ga_messages) + ga_add
        sync["ga_messages"] = ga_add

    return sync


# 内层 ToolNode，负责实际工具执行
_inner_tool_node = ToolNode(tools)


def agent_node(state: AgentState) -> dict:
    """Agent 推理节点。"""
    sync = _ensure_synced(state)
    response = llm_with_tools.invoke(state["messages"])

    return {
        "messages": sync.get("messages", []) + [response],
        "ga_messages": sync.get("ga_messages", []) + [langchain_to_agent_message(response)],
    }


def tool_node(state: AgentState) -> dict:
    """工具执行节点。"""
    sync = _ensure_synced(state)
    tool_result = _inner_tool_node.invoke({"messages": state["messages"]})
    lc_msgs = tool_result["messages"]

    return {
        "messages": sync.get("messages", []) + lc_msgs,
        "ga_messages": sync.get("ga_messages", []) + langchain_to_agent_messages(lc_msgs),
    }


builder = StateGraph(AgentState)

builder.add_node("agent", agent_node)
builder.add_node("tools", tool_node)

builder.add_edge(START, "agent")

builder.add_conditional_edges(
    "agent",
    tools_condition,
    {
        "tools": "tools",
        END: END,
    },
)

builder.add_edge("tools", "agent")

graph = builder.compile()
