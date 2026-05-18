import os

import pytest

from graph_agent.graph import graph
from graph_agent.message import create_user_message

pytestmark = pytest.mark.anyio

if not os.getenv("ANTHROPIC_API_KEY"):
    pytest.skip("Set ANTHROPIC_API_KEY to run integration tests.", allow_module_level=True)


async def test_simple_agent_smoke() -> None:
    """验证 Agent 能正确处理用户消息并返回结果。"""
    user_ga = create_user_message(
        "What is 19*3? Use tools if needed and answer with just the number."
    )
    result = await graph.ainvoke({"ga_messages": [user_ga]})

    # 验证两套消息列表长度一致
    assert len(result["messages"]) == len(result["ga_messages"])
    output_text = str(result["messages"][-1].content)
    assert "57" in output_text
