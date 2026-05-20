"""HookRegister — Hook 注册中心。

启动时扫描项目中所有 @hook 注解的方法，按检查点分组存储。
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable

from graph_agent.hook.base import HookType


class _HookEntry:
    """单个 Hook 的元信息。"""

    def __init__(self, func: Callable, checkpoint: str, priority: int,
                 hook_type: HookType, description: str, func_name: str):
        self.func = func
        self.checkpoint = checkpoint
        self.priority = priority
        self.hook_type = hook_type
        self.description = description
        self.func_name = func_name

    def __repr__(self) -> str:
        return (f"_HookEntry(name={self.func_name}, checkpoint={self.checkpoint}, "
                f"priority={self.priority}, type={self.hook_type.value})")


class HookRegister:
    """Hook 注册中心。

    启动时扫描项目中所有 @hook 注解的方法，按检查点分组存储。
    支持动态注册会话级 Hook（用于 ACP 事件收集等场景）。
    """

    def __init__(self):
        self._hooks: dict[str, list[_HookEntry]] = {
            "before_tool_call": [],
            "after_tool_call": [],
            "before_llm_call": [],
            "after_llm_call": [],
        }
        self._session_hooks: dict[str, list[_HookEntry]] = {
            "before_tool_call": [],
            "after_tool_call": [],
            "before_llm_call": [],
            "after_llm_call": [],
        }

    def register(self, func: Callable, checkpoint: str, priority: int,
                 hook_type: HookType, description: str) -> None:
        """注册一个 Hook 方法。"""
        entry = _HookEntry(
            func=func,
            checkpoint=checkpoint,
            priority=priority,
            hook_type=hook_type,
            description=description,
            func_name=getattr(func, "__name__", str(func)),
        )
        self._hooks[checkpoint].append(entry)
        self._hooks[checkpoint].sort(key=lambda e: e.priority)

    def auto_discover(self, package: str = "graph_agent") -> None:
        """自动扫描项目中所有 @hook 注解的方法并注册。"""
        try:
            pkg = importlib.import_module(package)
        except ImportError:
            return

        for _, mod_name, _ in pkgutil.walk_packages(
            pkg.__path__, prefix=package + "."
        ):
            try:
                module = importlib.import_module(mod_name)
            except ImportError:
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if callable(attr) and hasattr(attr, "__hook_meta__"):
                    meta = attr.__hook_meta__
                    self.register(
                        func=attr,
                        checkpoint=meta["checkpoint"],
                        priority=meta["priority"],
                        hook_type=meta["hook_type"],
                        description=meta["description"],
                    )

    def get_hooks(self, checkpoint: str) -> list[_HookEntry]:
        """获取指定检查点的 Hook 列表（已按优先级排序）。

        返回永久注册的 Hook 与当前会话级 Hook 合并后的列表。
        """
        permanent = self._hooks.get(checkpoint, [])
        session = self._session_hooks.get(checkpoint, [])
        if not session:
            return permanent
        combined = permanent + session
        combined.sort(key=lambda e: e.priority)
        return combined

    def add_session_hook(self, func: Callable, checkpoint: str, priority: int,
                         hook_type: HookType, description: str = "") -> None:
        """临时注册一个会话级 Hook（仅当前执行周期有效）。"""
        entry = _HookEntry(
            func=func,
            checkpoint=checkpoint,
            priority=priority,
            hook_type=hook_type,
            description=description,
            func_name=getattr(func, "__name__", str(func)),
        )
        self._session_hooks[checkpoint].append(entry)

    def clear_session_hooks(self) -> None:
        """清除所有会话级 Hook。"""
        for key in self._session_hooks:
            self._session_hooks[key].clear()

    def list_hooks(self) -> dict[str, list[str]]:
        """列出所有已注册 Hook 的名称（用于调试）。"""
        result: dict[str, list[str]] = {}
        for checkpoint, entries in self._hooks.items():
            names = [f"{e.func_name}(p={e.priority}, {e.hook_type.value})"
                     for e in entries]
            if names:
                result[checkpoint] = names
        return result
