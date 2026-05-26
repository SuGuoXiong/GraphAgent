"""用户长期偏好存储 —— Layer 2 扩展点。

基于文件存储的用户画像管理，提供偏好注入到 GuardAgent 上下文。
通过 MemoryManager 统一管理存储、提取和衰减。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_agent.message import MessageBlock
    from graph_agent.memory.memory_manager import MemoryManager


class UserPreferenceStore:
    """用户长期偏好存储 —— 委托给 MemoryManager 实现。

    作为 GuardContextBuilder 的 preference_store 参数注入，
    提供 to_context_message() 用于 Layer 2 上下文注入。
    """

    def __init__(self, manager: "MemoryManager | None" = None):
        self._manager = manager

    def set_manager(self, manager: "MemoryManager") -> None:
        self._manager = manager

    def get_preferences(self) -> dict | None:
        """获取当前用户画像（完整 dict）。"""
        if self._manager is None:
            return None
        prefs, _ = self._manager.load()
        return prefs

    def to_context_message(self) -> "MessageBlock | None":
        """将用户画像格式化为上下文消息（Layer 2 注入时调用）。"""
        prefs = self.get_preferences()
        if not prefs or self._manager is None:
            return None
        return self._manager.to_profile_message(prefs)
