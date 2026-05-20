"""HookExecutor — Hook 执行器。

按编排顺序执行同一检查点的所有 Hook：
  Type 1（MODIFY）Hook 链式传递修改后的 HookContext。
  Type 2（CONTROL）Hook 遇 SKIP/ABORT 立即终止后续 Hook 执行。
  Type 3（OBSERVE）Hook 静默执行，不影响数据流和控制流。
"""

from __future__ import annotations

import logging
from typing import Optional

from graph_agent.hook.base import (
    HookType, HookAction, HookDecision, HookContext, HookAbortError,
)
from graph_agent.hook.registry import HookRegister

logger = logging.getLogger("graph_agent.hook")


class HookExecutor:
    """Hook 执行器。

    按编排顺序执行同一检查点的所有 Hook。
    """

    def __init__(self, register: HookRegister):
        self._register = register

    @property
    def register(self) -> HookRegister:
        return self._register

    def execute(self, checkpoint: str, ctx: HookContext) -> tuple[HookContext, HookDecision | None]:
        """执行指定检查点的所有 Hook。

        Args:
            checkpoint: 检查点标识
            ctx: Hook 上下文

        Returns:
            (最终 HookContext, 流程决策)。决策为 None 表示 CONTINUE。
        """
        hooks = self._register.get_hooks(checkpoint)
        final_decision: HookDecision | None = None

        for entry in hooks:
            try:
                if entry.hook_type == HookType.MODIFY:
                    ctx = entry.func(ctx)
                elif entry.hook_type == HookType.CONTROL:
                    decision = entry.func(ctx)
                    if decision and decision.action in (HookAction.SKIP, HookAction.ABORT):
                        final_decision = decision
                        break
                elif entry.hook_type == HookType.OBSERVE:
                    entry.func(ctx)
            except Exception as e:
                if entry.hook_type == HookType.CONTROL:
                    raise HookAbortError(
                        f"Control hook '{entry.func_name}' 异常: {e}"
                    ) from e
                # Type 1 / Type 3 异常不阻断主流程
                logger.warning(
                    "Hook '%s' (checkpoint=%s, type=%s) 执行异常: %s",
                    entry.func_name, checkpoint, entry.hook_type.value, e,
                )

        return ctx, final_decision
