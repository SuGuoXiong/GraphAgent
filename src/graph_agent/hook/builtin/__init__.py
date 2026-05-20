"""内置 Hook 实现。

包含从 tracer 模块迁移的终端输出 Hook（tracer_hooks.py），
以及供参考的审计日志等内置 Hook。
"""

from graph_agent.hook.builtin import tracer_hooks  # noqa: F401 — 触发 @hook 注册
