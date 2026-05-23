"""RBAC 权限校验 Hook —— 在工具调用前进行策略匹配和权限判定。

优先级 20，在 AskUserInterceptor (10) 和 AuditPreRecord (15) 之后执行。
"""

import time

from graph_agent.hook.base import hook, HookContext, HookDecision, HookAction, HookType
from graph_agent.acp.checkpoint import AskUserException


@hook(
    checkpoint="before_tool_call",
    priority=20,
    hook_type=HookType.CONTROL,
    description="RBAC 权限校验"
)
def rbac_check(ctx: HookContext) -> HookDecision:
    """在工具调用前进行 RBAC 权限校验。"""
    from graph_agent.security import get_rbac_engine, extract_resource
    from graph_agent.security import AuditRecord, get_audit_logger
    from graph_agent.tools.base import ToolCenter

    engine = get_rbac_engine()
    subject = ctx.agent_name or "unknown"
    tool_name = ctx.tool_name
    tool_args = ctx.tool_args or {}

    tool_center = ToolCenter()
    tool_center.auto_discover()
    tool = tool_center.get_tool(tool_name) if tool_name else None
    if not tool:
        return HookDecision(action=HookAction.CONTINUE)

    action = tool_name
    resource = extract_resource(tool_name, tool_args)

    # 检查临时授权令牌
    pending = (ctx.agent_state or {}).get("_rbac_pending_escalation")
    if pending and _token_matches(pending, tool_name, resource):
        if pending.get("approved") and not _token_expired(pending):
            return HookDecision(action=HookAction.CONTINUE)
        elif pending.get("denied"):
            prerecord = (ctx.agent_state or {}).get("_audit_prerecord")
            if prerecord:
                record = AuditRecord.from_dict(prerecord)
                record.status = "denied"
                record.escalated = True
                get_audit_logger().write(record)
                if ctx.agent_state:
                    del ctx.agent_state["_audit_prerecord"]
            if ctx.agent_state:
                ctx.agent_state.pop("_rbac_pending_escalation", None)
            return HookDecision(action=HookAction.ABORT, reason="用户拒绝授权")

    decision = engine.evaluate(subject, action, resource)

    if decision == "deny":
        prerecord = (ctx.agent_state or {}).get("_audit_prerecord")
        if prerecord:
            record = AuditRecord.from_dict(prerecord)
            record.status = "denied"
            get_audit_logger().write(record)
            if ctx.agent_state:
                del ctx.agent_state["_audit_prerecord"]
        return HookDecision(
            action=HookAction.ABORT,
            reason=f"RBAC 拒绝: subject={subject}, resource={resource}, "
                   f"action={action}"
        )

    if decision == "need_escalation":
        _create_escalation_token(ctx, subject, tool_name, resource)
        raise AskUserException(
            question=f"Agent '{subject}' 请求执行操作:\n"
                     f"  工具: {tool_name}\n"
                     f"  对象: {resource}\n"
                     f"是否授权?",
            require_approval=True,
            state=ctx.agent_state,
        )

    return HookDecision(action=HookAction.CONTINUE)


def _token_matches(token: dict, tool_name: str, resource: str) -> bool:
    """检查令牌是否匹配当前工具调用。

    令牌中的 resource 支持前缀匹配。
    """
    if token.get("tool_name") != tool_name:
        return False
    token_resource = token.get("resource", "")
    return resource == token_resource or resource.startswith(token_resource)


def _token_expired(token: dict) -> bool:
    """检查令牌是否已过期。"""
    return time.time() > token.get("expires_at", 0)


def _create_escalation_token(ctx: HookContext, subject: str, tool_name: str,
                              resource: str) -> dict:
    """生成一次性授权令牌并存储到 agent_state。"""
    import uuid
    token = {
        "id": uuid.uuid4().hex[:8],
        "subject": subject,
        "tool_name": tool_name,
        "resource": resource,
        "created_at": time.time(),
        "expires_at": time.time() + 60,
        "approved": False,
        "denied": False,
    }
    if ctx.agent_state is None:
        ctx.agent_state = {}
    ctx.agent_state["_rbac_pending_escalation"] = token
    prerecord = ctx.agent_state.get("_audit_prerecord")
    if prerecord:
        prerecord["escalated"] = True
    return token
