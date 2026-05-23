"""安全模块 —— RBAC 鉴权 + 审计日志。

提供:
- RBACConfig: 策略配置加载
- RBACEngine: 策略匹配引擎
- extract_resource: 资源提取器
- AuditRecord: 审计记录模型
- AuditLogger: 缓冲式审计日志写入器
- get_audit_logger: 获取全局 AuditLogger 单例
"""

from graph_agent.security.audit import AuditRecord, AuditLogger, get_audit_logger
from graph_agent.security.rbac_config import RBACConfig
from graph_agent.security.rbac_engine import RBACEngine, get_rbac_engine
from graph_agent.security.resource_extractor import extract_resource

__all__ = [
    "AuditRecord",
    "AuditLogger",
    "get_audit_logger",
    "RBACConfig",
    "RBACEngine",
    "get_rbac_engine",
    "extract_resource",
]
