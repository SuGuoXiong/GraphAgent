"""内置 Hook 实现。

包含从 tracer 模块迁移的终端输出 Hook（tracer_hooks.py），
以及 RBAC 权限校验、审计日志等安全 Hook。
"""

from graph_agent.hook.builtin import tracer_hooks  # noqa: F401 — 触发 @hook 注册
from graph_agent.hook.builtin import ask_user_hook  # noqa: F401
from graph_agent.hook.builtin import rbac_hook      # noqa: F401
from graph_agent.hook.builtin import audit_hook     # noqa: F401
