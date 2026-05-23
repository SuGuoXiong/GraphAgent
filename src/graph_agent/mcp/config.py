"""MCP 配置模型——定义 mcp_servers.json 的数据结构。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TransportType(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


@dataclass
class MCPServerConfig:
    """单个 MCP Server 的连接配置。"""

    name: str
    transport: TransportType

    # stdio 专用参数
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None

    # streamable-http 专用参数
    url: str | None = None
    headers: dict[str, str] | None = None
    timeout: float = 30.0
    sse_read_timeout: float = 300.0

    # 安全配置
    risk_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "MCPServerConfig":
        """从 JSON 字典构造配置对象。"""
        transport = TransportType(data["transport"])
        return cls(
            name=name,
            transport=transport,
            command=data.get("command"),
            args=data.get("args", []),
            env=data.get("env"),
            cwd=data.get("cwd"),
            url=data.get("url"),
            headers=data.get("headers"),
            timeout=data.get("timeout", 30.0),
            sse_read_timeout=data.get("sse_read_timeout", 300.0),
            risk_overrides=data.get("risk_overrides", {}),
        )


def load_mcp_config(config_path: str | Path = "mcp_servers.json") -> list[MCPServerConfig]:
    """从 JSON 文件加载 MCP 配置列表。

    文件不存在时返回空列表（MCP 为可选功能）。
    """
    config_path = Path(config_path)
    if not config_path.exists():
        return []

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    servers = data.get("mcpServers", {})
    return [MCPServerConfig.from_dict(name, cfg) for name, cfg in servers.items()]
