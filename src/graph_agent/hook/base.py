"""Hook 核心类型定义。

提供 HookType、HookAction、HookDecision、HookContext、HookAbortError
以及 @hook 装饰器。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class HookType(Enum):
    MODIFY = "modify"    # 可修改 HookContext 数据
    CONTROL = "control"  # 可返回 HookDecision 控制流程
    OBSERVE = "observe"  # 只读，不可修改数据或流程


class HookAction(Enum):
    CONTINUE = "continue"
    SKIP = "skip"
    ABORT = "abort"


@dataclass
class HookDecision:
    action: HookAction
    reason: str = ""
    fallback_result: str | None = None


@dataclass
class HookContext:
    checkpoint: str
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    tool_error: str | None = None
    llm_messages: list | None = None
    llm_model: str | None = None
    llm_caller: str | None = None
    llm_response: str | None = None
    llm_token_usage: dict | None = None
    session_id: str | None = None
    agent_state: dict | None = None


# Hook 函数类型别名
ModifyHook = Callable[[HookContext], HookContext]
ControlHook = Callable[[HookContext], HookDecision]
ObserveHook = Callable[[HookContext], None]
HookFunc = ModifyHook | ControlHook | ObserveHook


class HookAbortError(Exception):
    """Type 2 Hook 返回 ABORT 时抛出的异常。"""

    def __init__(self, reason: str = ""):
        self.reason = reason
        super().__init__(f"执行被 Hook 中断: {reason}")


def hook(
    checkpoint: str,
    priority: int = 100,
    hook_type: HookType = HookType.OBSERVE,
    description: str = "",
):
    """声明一个方法为 Hook。

    被装饰的函数会被 HookRegister.auto_discover() 自动发现并注册。

    Args:
        checkpoint: 检查点，取值为 "before_tool_call" | "after_tool_call"
                    | "before_llm_call" | "after_llm_call"
        priority: 优先级，数字越小越先执行，默认 100
        hook_type: Hook 类型
        description: Hook 描述
    """
    valid_checkpoints = {
        "before_tool_call", "after_tool_call",
        "before_llm_call", "after_llm_call",
    }
    if checkpoint not in valid_checkpoints:
        raise ValueError(
            f"无效检查点 '{checkpoint}'，有效值: {valid_checkpoints}"
        )

    def decorator(func: Callable):
        func.__hook_meta__ = {
            "checkpoint": checkpoint,
            "priority": priority,
            "hook_type": hook_type,
            "description": description,
            "func_name": func.__name__,
        }
        return func

    return decorator
