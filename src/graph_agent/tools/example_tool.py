"""工具添加示例模板。

复制此文件并修改以添加新工具。
"""

from graph_agent.tools.base import tool


@tool("example_tool", "这是一个示例工具，用于演示如何添加新工具")
def example_tool(input_text: str) -> str:
    """示例工具函数。

    Args:
        input_text: 输入文本

    Returns:
        处理结果
    """
    return f"示例工具处理结果: {input_text}"
