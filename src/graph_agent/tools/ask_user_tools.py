"""ask_user 工具 —— Agent 向用户提问的占位工具。

实际逻辑由 before_tool_call Type 2 Hook (ask_user_hook.py) 接管，
工具函数体永远不会被执行。
"""

from graph_agent.tools.base import tool


@tool(
    name="ask_user",
    description=(
        "当需要用户提供更多信息、在多个选项中做出选择、"
        "或者需要用户批准某个操作时使用此工具。"
        "参数 question 为需要向用户提问的问题文本；"
        "参数 options 为可选的选项列表，用户可以从中选择一个；"
        "参数 require_approval 为 True 时表示需要用户批准（展示确认/取消按钮）。"
    ),
)
def ask_user(
    question: str,
    options: list[str] | None = None,
    require_approval: bool = False,
) -> str:
    """向用户提问——实际逻辑由 before_tool_call Hook 接管。"""
    return ""
