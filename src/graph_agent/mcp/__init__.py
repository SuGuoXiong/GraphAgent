"""MCP 协议支持模块——外部工具的发现、注册与调用。

使用示例:
    from graph_agent.mcp import MCPManager
    from graph_agent.tools import ToolCenter

    tool_center = ToolCenter()
    tool_center.auto_discover()

    mcp_manager = MCPManager(tool_center)
    mcp_manager.setup()
"""

from graph_agent.mcp.config import MCPServerConfig, load_mcp_config
from graph_agent.mcp.manager import MCPManager
from graph_agent.mcp.tool_adapter import wrap_mcp_tools

__all__ = [
    "MCPServerConfig",
    "MCPManager",
    "load_mcp_config",
    "wrap_mcp_tools",
]
