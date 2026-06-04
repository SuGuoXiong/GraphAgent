"""记忆系统模块（v2 重构版）—— 细粒度记忆 + SQLite FTS5 全文检索。

提供记忆的存储、索引、检索、提取、注入全生命周期管理。

核心类:
    MemoryManager      — 统一入口，协调所有子系统
    MemoryStore         — 每日 Markdown 文件存储层
    MemoryIndexer       — SQLite FTS5 索引管理
    MemorySearcher      — 全文检索引擎
    MemoryExtractor     — LLM 驱动的记忆提取
    MemoryInjector      — 上下文格式化注入
    UserPreferenceStore — Layer 2 注入接口（用户偏好）
    AgentMemoryStore    — Layer 3 注入接口（主题记忆）
"""

from graph_agent.memory.memory_manager import MemoryManager
from graph_agent.memory.memory_store import MemoryStore
from graph_agent.memory.memory_indexer import MemoryIndexer
from graph_agent.memory.memory_searcher import MemorySearcher
from graph_agent.memory.memory_extractor import MemoryExtractor
from graph_agent.memory.memory_injector import MemoryInjector
from graph_agent.memory.memory_tool import search_memory, register_memory_tool
from graph_agent.memory.preference_store import UserPreferenceStore
from graph_agent.memory.agent_memory_store import AgentMemoryStore

__all__ = [
    "MemoryManager",
    "MemoryStore",
    "MemoryIndexer",
    "MemorySearcher",
    "MemoryExtractor",
    "MemoryInjector",
    "UserPreferenceStore",
    "AgentMemoryStore",
    "search_memory",
    "register_memory_tool",
]
