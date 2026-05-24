"""审计日志 Hook —— 两段式记录工具调用的完整生命周期。

audit_prerecord (OBSERVE, 优先 15): 在 RBAC 之前保存调用意图
audit_record (OBSERVE, 优先 590): 在工具执行后更新状态并写入日志
"""

import time
from datetime import datetime, timezone

from graph_agent.hook.base import hook, HookContext, HookType


@hook(
    checkpoint="before_tool_call",
    priority=15,
    hook_type=HookType.OBSERVE,
    description="审计预记录：在 RBAC 之前保存调用意图"
)
def audit_prerecord(ctx: HookContext):
    """在所有 CONTROL Hook 之前记录调用意图。

    将初始 AuditRecord (status="pending") 暂存到 agent_state，
    后续在 after_tool_call 或 RBAC 拒绝时更新状态并写入日志。
    """
    from graph_agent.security import AuditRecord, extract_resource
    from graph_agent.tools.base import ToolCenter

    subject = ctx.agent_name or "unknown"
    tool_name = ctx.tool_name or ""

    tool_center = ToolCenter()
    tool_center.auto_discover()
    try:
        tool = tool_center.get_tool(tool_name) if tool_name else None
    except KeyError:
        tool = None

    record = AuditRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        subject=subject,
        session_id=ctx.session_id or "",
        tool_name=tool_name,
        action=tool_name,
        resource=extract_resource(tool_name, ctx.tool_args or {}),
        risk_level=tool.risk_level if tool else "medium",
        parameters=AuditRecord._sanitize_params(ctx.tool_args or {}),
        status="pending",
    )
    if ctx.agent_state is None:
        ctx.agent_state = {}
    ctx.agent_state["_audit_prerecord"] = record.to_dict()


@hook(
    checkpoint="after_tool_call",
    priority=590,
    hook_type=HookType.OBSERVE,
    description="审计日志：写入已通过的调用结果"
)
def audit_record(ctx: HookContext):
    """在 after_tool_call 更新审计记录并写入。

    仅当工具调用通过 RBAC 检查后才会执行到此处。
    从 agent_state 读取预记录的数据，更新 result 和 status，写入日志。
    """
    from graph_agent.security import AuditRecord, get_audit_logger

    prerecord = (ctx.agent_state or {}).get("_audit_prerecord")
    if not prerecord:
        return

    record = AuditRecord.from_dict(prerecord)
    record.status = "error" if ctx.tool_error else "allowed"
    record.result_summary = AuditRecord._truncate(str(ctx.tool_result or ""), 200)
    record.error_message = ctx.tool_error or ""

    pending = (ctx.agent_state or {}).get("_rbac_pending_escalation")
    if pending and pending.get("approved") and not _token_expired(pending):
        record.escalated = True
        record.escalation_approved = True
        record.escalation_reason = f"用户批准: {pending.get('id', '')}"

    if ctx.agent_state:
        ctx.agent_state.pop("_audit_prerecord", None)
        ctx.agent_state.pop("_rbac_pending_escalation", None)

    get_audit_logger().write(record)


def _token_expired(token: dict) -> bool:
    """检查令牌是否已过期。"""
    return time.time() > token.get("expires_at", 0)
