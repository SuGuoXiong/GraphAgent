"""用户长期偏好存储 —— Layer 2 扩展点（空壳实现）。

未来在此模块中实现用户偏好的持久化存储、自动发现和衰减机制。
当前空壳实现直接返回空结果，不影响主流程。
"""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_agent.message import MessageBlock


class UserPreferenceStore(ABC):
    """用户长期偏好存储（抽象基类，当前为空壳实现）。

    使用具体方法而非 @abstractmethod，使得未实现子类化时仍可
    直接实例化，表现为"无偏好"（空壳模式）。

    未来实现方向：
    - 存储后端：SQLite / JSON 文件
    - 偏好发现：从 GuardAgent 的审核反馈中自动提取
    - 偏好衰减：长时间未确认的偏好自动过期
    """

    def get_preferences(self, user_id: str) -> dict:
        """获取用户偏好，返回 {key: value} 字典。"""
        return {}

    def update_preference(self, user_id: str, key: str, value) -> None:
        """更新单条偏好。"""
        pass

    def delete_preference(self, user_id: str, key: str) -> None:
        """删除单条偏好。"""
        pass

    def to_context_message(self, user_id: str) -> MessageBlock | None:
        """将用户偏好格式化为上下文消息（Layer 2 注入时调用）。"""
        prefs = self.get_preferences(user_id)
        if not prefs:
            return None
        from graph_agent.message import MessageBlock
        from graph_agent.message.message_type import MessageType

        lines = [f"- {k}: {v}" for k, v in prefs.items()]
        return MessageBlock(
            role="system",
            content="## 用户长期偏好\n" + "\n".join(lines),
            name="PreferenceStore",
            message_type=MessageType.SYSTEM_NOTIFICATION.value,
            message_id="",
            metadata={"source": "user_preference"},
        )
