"""ACP（Agent Client Protocol）模块 —— UI 与 GraphAgent 之间的标准化协议层。

提供:
    - ACPMessage: 统一的 JSON 信封消息
    - ACPServer: 传输无关的 Agent 服务端
    - SessionManager: 多会话并发管理
    - ACPClient: 客户端 SDK
    - 传输层: HTTP+SSE / stdio

使用方式（启动 HTTP+SSE 服务）:
    from graph_agent.acp import ACPServer, HTTPSSETransport, ACPConfig

    config = ACPConfig.from_yaml()
    server = ACPServer(config)
    transport = HTTPSSETransport(server, config)
    await transport.start()
"""

from graph_agent.acp.protocol import (
    ACPMessage,
    ACPConfig,
    SessionInfo,
    CompressionResult,
    RequestEvent,
    ResponseEvent,
    PushEvent,
    ErrorCode,
    error_http_status,
)
from graph_agent.acp.session_manager import SessionManager, ConversationContext
from graph_agent.acp.server import ACPServer
from graph_agent.acp.client import ACPClient
from graph_agent.acp.transports import ACPTransport, HTTPSSETransport

__all__ = [
    "ACPMessage",
    "ACPConfig",
    "SessionInfo",
    "CompressionResult",
    "RequestEvent",
    "ResponseEvent",
    "PushEvent",
    "ErrorCode",
    "error_http_status",
    "SessionManager",
    "ConversationContext",
    "ACPServer",
    "ACPClient",
    "ACPTransport",
    "HTTPSSETransport",
]
