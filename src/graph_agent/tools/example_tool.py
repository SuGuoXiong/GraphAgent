"""工具添加示例模板。

复制此文件并修改以添加新工具。
"""
from graph_agent.tools.base import tool


@tool("find_apple", "输入位置，返回指定位置有几个苹果")
def find_apple(location: str) -> str:
    """示例工具函数。

    Args:
        location: 输入文本

    Returns:
        处理结果
    """
    return f"已发现: {location}有1个苹果"
