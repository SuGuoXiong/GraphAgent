"""端到端测试用的 MCP Server —— 提供 echo / add / get_time 三个工具。

启动方式:
    python test_mcp_server.py
    # 默认为 stdio transport，由 MCPManager 作为子进程启动
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")


@mcp.tool(name="echo", description="返回输入的字符串，用于验证 MCP 通信是否正常")
def echo(message: str) -> str:
    """回显输入内容。"""
    return f"[test-server echo]: {message}"


@mcp.tool(name="add", description="计算两个数的和")
def add(a: float, b: float) -> str:
    """加法运算。"""
    result = a + b
    return f"{a} + {b} = {result}"


@mcp.tool(name="get_time", description="获取当前 UTC 时间的 ISO 格式字符串")
def get_time() -> str:
    """返回当前 UTC 时间。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    mcp.run(transport="stdio")
