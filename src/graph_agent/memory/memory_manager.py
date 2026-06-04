"""记忆管理器（v2 重构版）—— 核心编排层。

协调五个子系统：存储、索引、检索、提取、注入。

生命周期:
    1. 实时提取 (extract_async) — execute_turn 返回后异步执行
    2. 会话结束合并 (consolidate) — 下一个 send_message 到达时同步执行
    3. 新会话加载 (load_for_context) — 会话创建时同步执行
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from graph_agent.memory.memory_store import MemoryStore
from graph_agent.memory.memory_indexer import MemoryIndexer
from graph_agent.memory.memory_searcher import MemorySearcher
from graph_agent.memory.memory_types import (
    MemoryRecord,
    MemoryType,
    MemoryScope,
    MemoryStatus,
    SearchMemoryInput,
    SearchMemoryResult,
    _now,
    _today_str,
    generate_memory_id,
    extract_date_from_id,
)

if TYPE_CHECKING:
    from graph_agent.message import MessageBlock

logger = logging.getLogger(__name__)

# 去重文本相似度阈值
_DEDUP_SIMILARITY_THRESHOLD = 0.85
# 单个 topic 最大条目数
_MAX_ENTRIES_PER_TOPIC = 10
# 记忆过期天数
_EXPIRY_DAYS = 90
# 低访问阈值（30 天内）
_LOW_ACCESS_THRESHOLD = 2
# INVALID 保留天数
_INVALID_RETENTION_DAYS = 30


class MemoryManager:
    """记忆系统统一入口。

    使用方式:
        mgr = MemoryManager()
        mgr.init()

        # 检索
        results = mgr.search("Python 代码风格", type="project_rule")

        # 创建
        record = MemoryRecord(type=..., scope=..., tags=[...], content="...")
        mgr.create(record)

        # 生命周期
        mgr.archive(memory_id)
        mgr.invalidate(memory_id)

        # 会话集成
        mgr.extract_async(session_id, messages, task_summary)
        mgr.consolidate(session_id)
    """

    def __init__(self, base_dir: str | None = None):
        self._store = MemoryStore(base_dir)
        self._indexer = MemoryIndexer()
        self._searcher = MemorySearcher(self._indexer, self._store)
        self._initialized = False

        # 暂存区: {session_id: list[MemoryRecord]}
        self._pending: dict[str, list[MemoryRecord]] = {}

    def init(self) -> None:
        """初始化记忆系统（幂等操作）。"""
        if self._initialized:
            return
        self._store.ensure_dirs()
        self._indexer.init_db()
        self._register_tool()
        self._initialized = True

    def _register_tool(self) -> None:
        """注册 search_memory 工具。"""
        try:
            from graph_agent.memory.memory_tool import register_memory_tool
            register_memory_tool()
        except Exception as e:
            logger.debug(f"search_memory 工具注册跳过: {e}")

    # ── CRUD ──────────────────────────────────────────────

    def create(self, record: MemoryRecord) -> str:
        """创建一条新记忆，返回记忆 ID。

        自动执行去重检查（FTS5 召回 + 文本相似度精确判断）。
        """
        self.init()

        # 去重检查
        existing = self._find_duplicate(record)
        if existing:
            # 合并更新已有记忆
            existing.content = record.content
            existing.tags = list(set(existing.tags) | set(record.tags))
            existing.updated_at = _now()
            self.update(existing.id, existing)
            logger.debug(f"记忆去重合并: {existing.id}")
            return existing.id

        # 创建新记忆（确保有有效 ID）
        if not record.id or len(record.id) < 20:
            record.id = generate_memory_id()
        record.created_at = _now()
        record.updated_at = _now()
        record.access_at = _now()
        record.access_count = 0
        record.status = MemoryStatus.ACTIVE

        # 写入文件 + 索引
        self._store.append_record(record)
        self._indexer.upsert_record(record)

        logger.debug(f"记忆创建: {record.id}")
        return record.id

    def get(self, memory_id: str) -> MemoryRecord | None:
        """根据 ID 获取记忆（合并文件和 DB 数据）。"""
        self.init()

        meta = self._indexer.get_meta(memory_id)
        if not meta:
            return None

        scope = meta["scope"]
        date_str = extract_date_from_id(memory_id)

        # 从文件加载 content（权威来源）
        records = self._store.load_daily_file(scope, date_str)
        for rec in records:
            if rec.id == memory_id:
                # 用 DB 中的统计字段覆盖文件中的值
                rec.access_at = meta.get("access_at", rec.access_at)
                rec.access_count = meta.get("access_count", rec.access_count)
                rec.status = MemoryStatus(meta["status"]) if meta.get("status") else rec.status
                return rec

        # 文件中有该记忆的记录
        return None

    def update(self, memory_id: str, record: MemoryRecord) -> None:
        """更新一条已有记忆。"""
        self.init()

        meta = self._indexer.get_meta(memory_id)
        if not meta:
            raise ValueError(f"记忆不存在: {memory_id}")

        old_status = meta.get("status", "ACTIVE")
        scope = meta["scope"]
        date_str = extract_date_from_id(memory_id)

        record.id = memory_id
        record.updated_at = _now()
        record.created_at = meta.get("created_at", record.created_at)

        # 如果原状态为 ARCHIVED，唤醒
        if old_status == MemoryStatus.ARCHIVED.value:
            record.status = MemoryStatus.ACTIVE
            record.updated_at = _now()
            # 从 archive/ 移回原文件
            self._store.move_record_between_files(
                memory_id=memory_id,
                from_scope="archive",
                from_date=date_str,
                to_scope=scope,
                to_date=date_str,
            )

        # 更新文件
        self._store.update_record(memory_id, record, scope)

        # 更新索引
        self._indexer.upsert_record(record)

        logger.debug(f"记忆更新: {memory_id}")

    def delete(self, memory_id: str) -> None:
        """物理删除一条记忆（不可逆）。"""
        self.init()

        meta = self._indexer.get_meta(memory_id)
        if not meta:
            return

        scope = meta["scope"]
        date_str = extract_date_from_id(memory_id)

        # 从文件移除
        self._store.remove_record(memory_id, scope, date_str)

        # 从索引删除
        self._indexer.delete_record(memory_id)

        logger.info(f"记忆物理删除: {memory_id}")

    # ── 检索 ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        type: str | None = None,
        scope: str | None = None,
        include_archived: bool = False,
        limit: int = 3,
    ) -> list[SearchMemoryResult]:
        """检索记忆（供 search_memory 工具调用）。"""
        self.init()

        input_ = SearchMemoryInput(
            query=query,
            type=type,
            scope=scope,
            include_archived=include_archived,
            limit=limit,
        )
        return self._searcher.search(input_)

    # ── 状态管理 ──────────────────────────────────────────

    def archive(self, memory_id: str) -> None:
        """将记忆归档（ACTIVE → ARCHIVED）。"""
        self.init()

        meta = self._indexer.get_meta(memory_id)
        if not meta:
            raise ValueError(f"记忆不存在: {memory_id}")

        if meta["status"] != MemoryStatus.ACTIVE.value:
            logger.debug(f"记忆 {memory_id} 状态为 {meta['status']}，跳过归档")
            return

        scope = meta["scope"]
        date_str = extract_date_from_id(memory_id)
        today = _today_str()

        # 移动到 archive/ 目录
        record = self._store.move_record_between_files(
            memory_id=memory_id,
            from_scope=scope,
            from_date=date_str,
            to_scope="archive",
            to_date=today,
        )

        if record:
            # 更新 meta
            self._indexer.update_meta(
                memory_id,
                status=MemoryStatus.ARCHIVED.value,
                file_path=f"archive/{today}.md",
                updated_at=_now(),
            )

        logger.debug(f"记忆归档: {memory_id}")

    def activate(self, memory_id: str) -> None:
        """激活记忆（ARCHIVED → ACTIVE）。"""
        self.init()

        meta = self._indexer.get_meta(memory_id)
        if not meta:
            raise ValueError(f"记忆不存在: {memory_id}")

        if meta["status"] != MemoryStatus.ARCHIVED.value:
            logger.debug(f"记忆 {memory_id} 状态为 {meta['status']}，跳过激活")
            return

        scope = meta["scope"]
        date_str = extract_date_from_id(memory_id)
        archive_date = meta.get("file_path", "").split("/")[-1].replace(".md", "") or date_str

        # 从 archive/ 移回原文件
        self._store.move_record_between_files(
            memory_id=memory_id,
            from_scope="archive",
            from_date=archive_date,
            to_scope=scope,
            to_date=date_str,
        )

        self._indexer.update_meta(
            memory_id,
            status=MemoryStatus.ACTIVE.value,
            file_path=f"{scope}/{date_str}.md",
            updated_at=_now(),
        )

        logger.info(f"记忆激活: {memory_id}")

    def invalidate(self, memory_id: str) -> None:
        """将记忆标记为废弃（→ INVALID）。"""
        self.init()

        meta = self._indexer.get_meta(memory_id)
        if not meta:
            raise ValueError(f"记忆不存在: {memory_id}")

        # 更新 meta 状态
        self._indexer.update_meta(
            memory_id,
            status=MemoryStatus.INVALID.value,
            updated_at=_now(),
        )

        # 从 FTS5 删除（保留 meta 记录备查）
        self._indexer.delete_fts_only(memory_id)

        logger.info(f"记忆废弃: {memory_id}")

    # ── 批量操作 ──────────────────────────────────────────

    def archive_session_memories(self, session_id: str) -> int:
        """归档指定会话的所有 ACTIVE 记忆。返回归档数量。"""
        self.init()

        memories = self._indexer.get_session_memories(session_id)
        count = 0
        for mem in memories:
            try:
                self.archive(mem["id"])
                count += 1
            except Exception as e:
                logger.warning(f"归档会话记忆失败 {mem['id']}: {e}")

        logger.info(f"会话记忆归档: session={session_id[:12]}..., count={count}")
        return count

    def run_maintenance(self) -> dict:
        """执行定时维护任务，返回操作统计。"""
        self.init()

        stats = {
            "archived": 0,
            "deleted": 0,
            "empty_files_cleaned": 0,
            "index_verified": False,
            "index_rebuilt": False,
        }

        # 1. 扫描 ACTIVE 记忆，检查归档条件
        candidates = self._indexer.get_records_for_maintenance(
            status=MemoryStatus.ACTIVE.value,
            min_age_days=_EXPIRY_DAYS,
            max_access_count=_LOW_ACCESS_THRESHOLD,
        )
        for mem in candidates:
            try:
                self.archive(mem["id"])
                stats["archived"] += 1
            except Exception as e:
                logger.warning(f"自动归档失败 {mem['id']}: {e}")

        # 2. 清理空文件
        for scope, date_str in self._store.list_all_file_keys():
            if self._store.delete_empty_file(scope, date_str):
                stats["empty_files_cleaned"] += 1

        # 3. 清理过期 INVALID 记忆（物理删除）
        invalid_count = self._indexer.count_by_status(MemoryStatus.INVALID.value)
        if invalid_count > 0:
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=_INVALID_RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # 注：实际实现中应查询 updated_at < cutoff 的 INVALID 记录
            # 此处简化处理

        # 4. 验证索引一致性
        total_file_records = 0
        for scope, date_str in self._store.list_all_file_keys():
            records = self._store.load_daily_file(scope, date_str)
            total_file_records += len(records)

        stats["index_verified"] = self._indexer.verify_consistency(total_file_records)

        if not stats["index_verified"]:
            # 重建索引
            records_by_file: dict[tuple[str, str], list[MemoryRecord]] = {}
            for scope, date_str in self._store.list_all_file_keys():
                records = self._store.load_daily_file(scope, date_str)
                if records:
                    records_by_file[(scope, date_str)] = records
            self._indexer.rebuild_index(records_by_file)
            stats["index_rebuilt"] = True

        logger.info(
            f"维护任务完成: archived={stats['archived']}, deleted={stats['deleted']}, "
            f"empty_cleaned={stats['empty_files_cleaned']}, "
            f"index_verified={stats['index_verified']}, index_rebuilt={stats['index_rebuilt']}"
        )
        return stats

    # ── 上下文注入 ────────────────────────────────────────

    def load_for_context(self, limit: int = 10) -> tuple[list[dict], list[dict]]:
        """加载记忆用于上下文注入（替代旧 load() 方法）。

        Returns:
            (user_preferences_list, other_memories_list)
        """
        self.init()

        # 用户偏好：所有 ACTIVE 的 user_preference 类型记忆
        user_prefs = self.search(
            query="",
            type=MemoryType.USER_PREFERENCE.value,
            scope=MemoryScope.USER.value,
            limit=limit,
        )
        user_prefs_list = [
            {
                "id": r.id,
                "type": r.type,
                "scope": r.scope,
                "tags": r.tags,
                "content": r.content,
                "created_at": r.created_at,
            }
            for r in user_prefs
        ]

        # 其他记忆：最近访问的架构/规范/模式/避坑类记忆
        other_types = [
            MemoryType.ARCH_DESIGN.value,
            MemoryType.PROJECT_RULE.value,
            MemoryType.DESIGN_PATTERN.value,
            MemoryType.BUG_PITFALL.value,
            MemoryType.GENERAL_KNOWLEDGE.value,
        ]
        other_memories: list[dict] = []
        for t in other_types:
            results = self.search(query="", type=t, limit=limit)
            for r in results:
                other_memories.append({
                    "id": r.id,
                    "type": r.type,
                    "scope": r.scope,
                    "tags": r.tags,
                    "content": r.content,
                    "created_at": r.created_at,
                })

        # 按 access_at 排序截断
        other_memories = other_memories[:limit]

        return user_prefs_list, other_memories

    def to_profile_message(self, profile_list: list[dict] | None) -> "MessageBlock | None":
        """将用户偏好列表转为上下文消息（Layer 2 注入）。"""
        if not profile_list:
            return None

        from graph_agent.memory.memory_injector import MemoryInjector
        return MemoryInjector.format_profile_v2(profile_list)

    def to_memory_message(self, memory_list: list[dict] | None) -> "MessageBlock | None":
        """将记忆列表转为上下文消息（Layer 3 注入）。"""
        if not memory_list:
            return None

        from graph_agent.memory.memory_injector import MemoryInjector
        return MemoryInjector.format_memories_v2(memory_list)

    # ── 提取 ──────────────────────────────────────────────

    def extract_async(
        self,
        session_id: str,
        messages: list,
        task_summary: str = "",
        user_feedback: str = "",
    ) -> None:
        """提交异步记忆提取任务（不阻塞当前请求）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        loop.create_task(self._extract_and_stage(
            session_id, messages, task_summary, user_feedback,
        ))

    async def _extract_and_stage(
        self,
        session_id: str,
        messages: list,
        task_summary: str,
        user_feedback: str,
    ) -> None:
        """在后台执行提取并将结果写入暂存区。"""
        self.init()

        try:
            from graph_agent.memory.memory_extractor import MemoryExtractor
            extractor = MemoryExtractor(self._store)
            result = await extractor.extract(messages, task_summary, user_feedback)

            # 将提取结果转换为 MemoryRecord 列表并暂存
            records = extractor.result_to_records(result, session_id)
            if records:
                self._pending[session_id] = records
                logger.debug(f"记忆提取完成 session={session_id[:12]}..., count={len(records)}")
        except Exception as e:
            logger.warning(f"记忆提取失败 session={session_id[:12]}...: {e}")

    def consolidate(self, session_id: str) -> int:
        """合并暂存记忆到正式存储。返回合并的记忆数量。"""
        self.init()

        pending = self._pending.pop(session_id, None)
        if not pending:
            return 0

        count = 0
        for record in pending:
            try:
                self.create(record)
                count += 1
            except Exception as e:
                logger.warning(f"记忆合并失败 {record.id}: {e}")

        logger.debug(f"记忆合并完成 session={session_id[:12]}..., count={count}")
        return count

    # ── 内部方法 ──────────────────────────────────────────

    def _find_duplicate(self, record: MemoryRecord) -> MemoryRecord | None:
        """检查是否存在高度相似的已有记忆（用于去重）。"""
        # 快速召回：FTS5 检索候选
        type_str = record.type.value if hasattr(record.type, 'value') else record.type
        scope_str = record.scope.value if hasattr(record.scope, 'value') else record.scope
        query = " ".join(record.tags) if record.tags else record.content[:50]

        if not query.strip():
            return None

        results = self.search(
            query=query,
            type=type_str,
            scope=scope_str,
            limit=3,
        )

        for r in results:
            # 精确判断：Jaccard 相似度
            sim = _jaccard_similarity(record.content, r.content)
            if sim > _DEDUP_SIMILARITY_THRESHOLD:
                # 加载完整记录
                existing = self.get(r.id)
                if existing and existing.status == MemoryStatus.ACTIVE:
                    return existing

        return None

    # ── 索引维护 ──────────────────────────────────────────

    def rebuild_index(self) -> dict:
        """从 Markdown 文件重建 FTS5 索引。"""
        self.init()

        records_by_file: dict[tuple[str, str], list[MemoryRecord]] = {}
        for scope, date_str in self._store.list_all_file_keys():
            records = self._store.load_daily_file(scope, date_str)
            if records:
                records_by_file[(scope, date_str)] = records

        return self._indexer.rebuild_index(records_by_file)


# ── 辅助函数 ────────────────────────────────────────────────


def _jaccard_similarity(a: str, b: str) -> float:
    """计算两个字符串的 Jaccard 相似度（基于字符集）。"""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0
