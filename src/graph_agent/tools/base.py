"""GraphAgent 工具模块核心基础类。

提供工具定义、注册和管理功能。
"""
import sys
import importlib
from pathlib import Path
from typing import Any, Callable
from inspect import signature

from langchain_core.tools import StructuredTool


class AgentTool:
    """封装单个工具的元数据和执行逻辑。"""

    name: str
    description: str
    parameters: list[dict[str, str]]
    func: Callable[..., str]

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., str],
        parameters: list[dict[str, str]]
    ):
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters

    def run(self, **kwargs: Any) -> str:
        """执行工具逻辑。"""
        return self.func(**kwargs)

    def to_langchain_tool(self) -> StructuredTool:
        """转换为 LangChain 兼容的工具。"""
        return StructuredTool.from_function(
            func=self.func,
            name=self.name,
            description=self.description
        )


class ToolCenter:
    """工具管理中心。

    提供工具的注册、发现和查询功能。
    """

    tools: dict[str, AgentTool]

    def __init__(self) -> None:
        self.tools = {}

    def register(self, tool: AgentTool) -> None:
        """手动注册工具。"""
        self.tools[tool.name] = tool

    def unregister(self, tool_name: str) -> None:
        """注销工具。"""
        if tool_name in self.tools:
            del self.tools[tool_name]

    def get_tool(self, name: str) -> AgentTool:
        """获取指定工具。"""
        return self.tools[name]

    def list_tools(self) -> list[AgentTool]:
        """列出所有已注册工具。"""
        return list(self.tools.values())

    def get_langchain_tools(self) -> list[StructuredTool]:
        """转换为 LangChain 兼容的工具列表。"""
        return [tool.to_langchain_tool() for tool in self.tools.values()]

    def auto_discover(self, tools_package: str = "graph_agent.tools") -> None:
        """自动扫描并注册 tools 目录下所有使用 @tool 装饰器的工具。"""
        try:
            package = importlib.import_module(tools_package)
        except ImportError:
            package_path = Path(__file__).parent
            sys.path.insert(0, str(package_path.parent.parent))
            package = importlib.import_module(tools_package)

        package_path = Path(package.__file__).parent

        for file_path in package_path.glob("*.py"):
            if file_path.name.startswith("_") or file_path.name == "base.py":
                continue

            module_name = f"{tools_package}.{file_path.stem}"
            if module_name in sys.modules:
                module = sys.modules[module_name]
            else:
                module = importlib.import_module(module_name)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, AgentTool):
                    self.register(attr)


def tool(name: str, description: str):
    """工具装饰器，用于定义 Agent 工具。

    Args:
        name: 工具名称
        description: 工具功能描述，供 LLM 理解
    """
    def decorator(func: Callable[..., str]) -> AgentTool:
        sig = signature(func)
        parameters = []

        for param in sig.parameters.values():
            parameters.append({
                "name": param.name,
                "type": str(param.annotation) if param.annotation != param.empty else "Any"
            })

        return AgentTool(name, description, func, parameters)

    return decorator
