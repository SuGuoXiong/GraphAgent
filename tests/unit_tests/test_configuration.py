import pytest

from graph_agent.graph import graph, tool_center
from graph_agent.state import AgentState

pytestmark = pytest.mark.anyio


def test_graph_compiles() -> None:
    """验证图已正确编译。"""
    assert graph is not None


def test_agent_state_fields() -> None:
    """验证 AgentState 同时包含 messages（继承自 MessagesState）和 ga_messages。"""
    annotations = AgentState.__annotations__
    assert "messages" in annotations
    assert "ga_messages" in annotations
    assert "cur_iteration" in annotations
    assert "max_iteration" in annotations


def test_tools_registered() -> None:
    """验证工具自动发现已注册工具。"""
    tool_names = [t.name for t in tool_center.list_tools()]
    assert len(tool_names) > 0
