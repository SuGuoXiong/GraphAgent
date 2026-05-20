"""GraphAgent Hook 模块。

提供统一的 Hook 机制，在工具调用前/后、LLM 调用前/后四个检查点
插入数据修改、流程控制、纯观测三类 Hook。

使用示例:
    from graph_agent.hook import hook, HookContext, HookDecision, HookType, HookAction, get_hook_executor

    @hook("before_tool_call", priority=10, hook_type=HookType.MODIFY)
    def validate_params(ctx: HookContext) -> HookContext:
        ...
        return ctx
"""

from graph_agent.hook.base import (
    HookType,
    HookAction,
    HookDecision,
    HookContext,
    HookAbortError,
    ModifyHook,
    ControlHook,
    ObserveHook,
    HookFunc,
    hook,
)
from graph_agent.hook.registry import HookRegister, _HookEntry
from graph_agent.hook.executor import HookExecutor

__all__ = [
    "HookType",
    "HookAction",
    "HookDecision",
    "HookContext",
    "HookAbortError",
    "ModifyHook",
    "ControlHook",
    "ObserveHook",
    "HookFunc",
    "hook",
    "HookRegister",
    "HookExecutor",
    "get_hook_executor",
    "init_hooks",
]


# 全局单例
_hook_executor: HookExecutor | None = None


def get_hook_executor() -> HookExecutor:
    """获取全局 HookExecutor 实例。

    首次调用时自动初始化（执行 auto_discover）。
    """
    global _hook_executor
    if _hook_executor is None:
        init_hooks()
    return _hook_executor  # type: ignore[return-value]


def init_hooks(package: str = "graph_agent") -> HookExecutor:
    """初始化 Hook 系统。

    执行 auto_discover 扫描所有 @hook 装饰的方法并注册。
    幂等操作：多次调用不会重复初始化。

    Args:
        package: 自动发现扫描的包名，默认 "graph_agent"
    """
    global _hook_executor
    if _hook_executor is not None:
        return _hook_executor

    register = HookRegister()
    register.auto_discover(package)

    import sys
    enabled = _is_hook_enabled()
    if enabled:
        hook_list = register.list_hooks()
        if hook_list:
            lines = ["[Hook] 已注册 Hook:"]
            for cp, names in hook_list.items():
                for n in names:
                    lines.append(f"  {cp}: {n}")
            print("\n".join(lines), file=sys.stderr)

    _hook_executor = HookExecutor(register)
    return _hook_executor


def _is_hook_enabled() -> bool:
    import os
    return os.getenv("GRAPHAGENT_HOOK_ENABLED", "true").lower() in ("true", "1", "yes")
