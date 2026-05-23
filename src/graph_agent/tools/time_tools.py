"""时间相关工具。"""

from datetime import datetime, timezone
from graph_agent.tools.base import tool


@tool("get_utc_time", "返回当前的UTC时间戳，使用ISO 8601格式", risk_level="low")
def utc_now() -> str:
    """返回当前UTC时间戳的ISO格式。"""
    return datetime.now(tz=timezone.utc).isoformat()
