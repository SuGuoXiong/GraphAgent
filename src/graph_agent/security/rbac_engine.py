"""RBAC 策略匹配引擎。"""

import fnmatch
from typing import Literal

EngineDecision = Literal["allow", "deny", "need_escalation"]


def _glob_match(text: str, pattern: str) -> bool:
    """支持 ** 递归匹配的 glob 匹配。

    ** 匹配任意层级目录（包括零层级），* 仅匹配单层（不跨越 /）。
    """
    import re
    parts = []
    i = 0
    while i < len(pattern):
        if pattern[i:i + 2] == "**":
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] in ".^$+{}()[]|\\":
            parts.append("\\" + pattern[i])
            i += 1
        else:
            parts.append(pattern[i])
            i += 1
    return re.match("^" + "".join(parts) + "$", text) is not None


class RBACEngine:
    """RBAC 策略匹配引擎。

    按策略列表顺序遍历，首次匹配即生效。无匹配策略时返回 need_escalation。
    """

    def __init__(self, config: "RBACConfig"):  # noqa: F821
        self.config = config

    def evaluate(self, subject: str, action: str, resource: str) -> EngineDecision:
        """评估一次工具调用是否被允许。

        Args:
            subject: Agent 身份标识
            action: 工具名称
            resource: 操作目标

        Returns:
            "allow": 策略 effect: allow 匹配
            "deny": 策略 effect: deny 匹配
            "need_escalation": 无策略匹配，需用户授权
        """
        for policy in self.config.policies:
            if not self._match_subject(policy, subject):
                continue
            if not self._match_action(policy, action):
                continue
            if not self._match_resource(policy, resource):
                continue
            return policy.get("effect", "deny")

        return "need_escalation"

    def _match_subject(self, policy: dict, subject: str) -> bool:
        expected = policy.get("subject", "")
        if expected == "*":
            return True
        return expected == subject

    def _match_action(self, policy: dict, action: str) -> bool:
        expected = policy.get("action", "")
        if expected == "*":
            return True
        return fnmatch.fnmatch(action, expected)

    def _match_resource(self, policy: dict, resource: str) -> bool:
        expected = policy.get("resource", "")
        if expected == "*":
            return True

        norm_resource = resource.replace("\\", "/")
        norm_expected = expected.replace("\\", "/")

        prefix, _, _ = norm_expected.partition(":")

        if prefix == "file":
            return _glob_match(norm_resource, norm_expected)
        elif prefix == "dir":
            return norm_resource == norm_expected or \
                   norm_resource.startswith(norm_expected.rstrip("/") + "/")
        elif prefix == "cmd":
            return fnmatch.fnmatch(norm_resource, norm_expected)

        return norm_resource == norm_expected


# 全局单例
_rbac_engine: RBACEngine | None = None


def get_rbac_engine(config_path: str = "config/rbac.yaml") -> RBACEngine:
    """获取全局 RBACEngine 单例。"""
    global _rbac_engine
    if _rbac_engine is None:
        from graph_agent.security.rbac_config import RBACConfig
        _rbac_engine = RBACEngine(RBACConfig(config_path))
    return _rbac_engine
