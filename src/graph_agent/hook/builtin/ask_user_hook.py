"""ask_user 拦截 Hook —— 在 ask_user 工具调用前中断会话并向用户提问。

优先级 10（在所有其他 Hook 之前执行），确保在日志记录等 Hook 之前
就拦截 ask_user 调用并抛出 AskUserException。
"""

from graph_agent.hook.base import hook, HookContext, HookDecision, HookAction, HookType
from graph_agent.acp.checkpoint import AskUserException


@hook(
    checkpoint="before_tool_call",
    priority=10,
    hook_type=HookType.CONTROL,
    description="拦截 ask_user 工具调用，中断会话并向用户提问"
)
def ask_user_interceptor(ctx: HookContext) -> HookDecision:
    if ctx.tool_name != "ask_user":
        return HookDecision(action=HookAction.CONTINUE)

    question = ctx.tool_args.get("question", "") if ctx.tool_args else ""
    options = ctx.tool_args.get("options") if ctx.tool_args else None
    require_approval = ctx.tool_args.get("require_approval", False) if ctx.tool_args else False

    raise AskUserException(
        question=question,
        options=options,
        require_approval=require_approval,
        state=ctx.agent_state,
    )
