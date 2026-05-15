from typing import Annotated
import operator

from langgraph.graph import MessagesState

from graph_agent.message import MessageBlock


class AgentState(MessagesState):
    """Agent 状态，同时维护 LangChain 原生消息和自定义 MessageBlock。

    messages (继承自 MessagesState):   LangChain BaseMessage 列表，供 LLM/ToolNode 直接使用
    ga_messages:                       自定义 MessageBlock 列表，供外部消费
    """
    ga_messages: Annotated[list[MessageBlock], operator.add]
    cur_iteration: int = 0
    max_iteration: int = 10
