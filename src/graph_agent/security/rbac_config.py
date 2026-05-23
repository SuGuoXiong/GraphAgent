"""RBAC 配置加载器。"""

import yaml
from pathlib import Path


class RBACConfig:
    """RBAC 策略配置加载与管理。

    策略按列表顺序排列，首次匹配即生效。配置不存在时静默降级为空策略列表。
    """

    def __init__(self, config_path: str = "config/rbac.yaml"):
        self.config_path = Path(config_path)
        self.policies: list[dict] = []
        self._load()

    def _load(self):
        if not self.config_path.exists():
            return
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self.policies = data.get("policies", [])

    def reload(self):
        """热加载配置（无需重启）。"""
        self._load()
