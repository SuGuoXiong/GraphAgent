"""传输层抽象基类 —— 定义 ACP 传输层的统一接口。

所有具体传输实现（HTTP/SSE、WebSocket、stdio）均实现此接口，
使得 ACPServer 可以绑定任意传输层而无须关心底层通信细节。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Awaitable

from graph_agent.acp.protocol import ACPMessage

RequestHandler = Callable[[str, ACPMessage], Awaitable[list[ACPMessage]]]


class ACPTransport(ABC):
    """ACP 传输层抽象基类。

    每个传输实现负责:
    1. 监听/接收来自客户端的请求消息
    2. 将请求转发给 RequestHandler 并收集回复/事件
    3. 将回复/事件序列化后发回客户端
    """

    @abstractmethod
    async def start(self) -> None:
        """启动传输层，开始监听连接。"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止传输层，关闭所有连接。"""
        ...

    @abstractmethod
    def set_handler(self, handler: RequestHandler) -> None:
        """设置请求处理器 —— ACPServer 通过此方法注册消息处理回调。"""
        ...
