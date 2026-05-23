"""计算相关工具。"""

import ast
from typing import Any
from graph_agent.tools.base import tool


@tool("safe_calculator", "安全地计算简单的算术表达式，支持 +, -, *, /, %, ** 和括号", risk_level="low")
def calculator(expression: str) -> str:
    """计算算术表达式。

    Args:
        expression: 要计算的算术表达式字符串

    Returns:
        计算结果的字符串表示
    """
    parsed = ast.parse(expression, mode="eval")
    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Load,
    )

    for node in ast.walk(parsed):
        if not isinstance(node, allowed_nodes):
            raise ValueError("表达式包含不支持的语法")

    result: Any = eval(compile(parsed, "<calculator>", "eval"), {"__builtins__": {}}, {})
    return str(result)
