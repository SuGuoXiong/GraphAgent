"""文档存储层。

提供文档文件管理、SQLite FTS5 全文索引、知识库注册表和 CRUD 操作。
复用记忆系统的 FTS5 索引策略（unicode61 分词器、中英文混合）。
"""

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from graph_agent.rag.rag_config import KnowledgeBaseConfig
from graph_agent.rag.rag_types import (
    Chunk,
    Document,
    DocumentStatus,
    EmbedderType,
    KnowledgeBase,
    VectorStoreType,
)

logger = logging.getLogger(__name__)


def _generate_id() -> str:
    """生成带时间戳的唯一 ID。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{uuid.uuid4().hex[:8]}"


def _compute_hash(content: str) -> str:
    """计算内容的 SHA256 哈希。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class KnowledgeBaseManager:
    """知识库注册表管理。

    维护 data/rag/kb_registry.json，提供知识库的 CRUD 操作。
    """

    CURRENT_SCHEMA_VERSION = 1

    def __init__(self, data_dir: str = "data/rag"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.data_dir / "kb_registry.json"
        self._registry: dict[str, dict] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """加载知识库注册表。"""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    self._registry = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("kb_registry.json 损坏，将从磁盘重建: %s", e)
                self._registry = {}
                self._rebuild_from_disk()
        else:
            self._registry = {}

    def _save_registry(self) -> None:
        """持久化知识库注册表。"""
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(self._registry, f, ensure_ascii=False, indent=2)

    def _rebuild_from_disk(self) -> list[str]:
        """从磁盘扫描重建注册表。

        遍历 data/rag/ 下所有包含 kb_config.yaml 的目录。
        """
        recovered = []
        if not self.data_dir.exists():
            return recovered

        for entry in self.data_dir.iterdir():
            if not entry.is_dir():
                continue
            kb_config_path = entry / "kb_config.yaml"
            if kb_config_path.exists():
                try:
                    kb_config = KnowledgeBaseConfig.from_yaml(kb_config_path)
                    kb_name = kb_config.name or entry.name
                    self._registry[kb_name] = {
                        "name": kb_name,
                        "path": str(entry),
                    }
                    recovered.append(kb_name)
                    logger.info("从磁盘恢复知识库: %s", kb_name)
                except Exception as e:
                    logger.warning("无法恢复目录 %s 的知识库: %s", entry, e)

        if recovered:
            self._save_registry()
        return recovered

    def is_registry_healthy(self) -> bool:
        """检查注册表完整性。"""
        if not self.registry_path.exists():
            return False
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return False

        for kb_name, info in data.items():
            kb_dir = Path(info.get("path", ""))
            if not kb_dir.exists():
                return False
            if not (kb_dir / "kb_config.yaml").exists():
                return False
        return True

    def list_kbs(self) -> list[dict]:
        """列出所有知识库的基本信息。"""
        result = []
        for kb_name, info in self._registry.items():
            kb_dir = Path(info["path"])
            document_count = 0
            chunk_count = 0
            embedding_model = ""
            embedding_dimension = 0

            kb_config_path = kb_dir / "kb_config.yaml"
            if kb_config_path.exists():
                try:
                    kb_config = KnowledgeBaseConfig.from_yaml(kb_config_path)
                    embedding_model = kb_config.embedder_model
                except Exception:
                    pass

            # 尝试从 DocumentStore 获取统计
            doc_store = DocumentStore(kb_name, str(kb_dir))
            stats = doc_store.get_stats()
            document_count = stats.get("document_count", 0)
            chunk_count = stats.get("chunk_count", 0)
            embedding_dimension = stats.get("embedding_dimension", 0)

            result.append({
                "name": kb_name,
                "document_count": document_count,
                "chunk_count": chunk_count,
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
            })
        return result

    def create_kb(self, name: str, description: str = "",
                  kb_config: KnowledgeBaseConfig | None = None) -> KnowledgeBase:
        """创建新知识库。"""
        if name in self._registry:
            raise ValueError(f"知识库 '{name}' 已存在")

        kb_dir = self.data_dir / name
        kb_dir.mkdir(parents=True, exist_ok=True)

        if kb_config is None:
            kb_config = KnowledgeBaseConfig(name=name, description=description)
        else:
            kb_config.name = name
            kb_config.description = description

        kb_config.to_yaml(kb_dir / "kb_config.yaml")

        self._registry[name] = {"name": name, "path": str(kb_dir)}
        self._save_registry()

        # 初始化 DocumentStore
        doc_store = DocumentStore(name, str(kb_dir))
        doc_store.initialize()

        return KnowledgeBase(
            name=name,
            description=description,
            embedder_type=kb_config.embedder_provider,
            embedding_model=kb_config.embedder_model,
            chunk_strategy=kb_config.chunk_strategy,
            chunk_size=kb_config.chunk_size,
            chunk_overlap=kb_config.chunk_overlap,
            keyword_weight=kb_config.keyword_weight,
            vector_weight=kb_config.vector_weight,
            similarity_threshold=kb_config.similarity_threshold,
        )

    def get_kb(self, name: str) -> KnowledgeBase | None:
        """获取知识库信息。"""
        if name not in self._registry:
            return None

        info = self._registry[name]
        kb_dir = Path(info["path"])
        kb_config_path = kb_dir / "kb_config.yaml"

        if not kb_config_path.exists():
            return None

        kb_config = KnowledgeBaseConfig.from_yaml(kb_config_path)
        doc_store = DocumentStore(name, str(kb_dir))
        stats = doc_store.get_stats()

        return KnowledgeBase(
            name=name,
            description=kb_config.description,
            embedder_type=kb_config.embedder_provider,
            embedding_model=kb_config.embedder_model,
            embedding_dimension=stats.get("embedding_dimension", 0),
            chunk_strategy=kb_config.chunk_strategy,
            chunk_size=kb_config.chunk_size,
            chunk_overlap=kb_config.chunk_overlap,
            keyword_weight=kb_config.keyword_weight,
            vector_weight=kb_config.vector_weight,
            similarity_threshold=kb_config.similarity_threshold,
            document_count=stats.get("document_count", 0),
            chunk_count=stats.get("chunk_count", 0),
        )

    def delete_kb(self, name: str) -> bool:
        """删除知识库及其所有数据。"""
        if name not in self._registry:
            return False

        info = self._registry.pop(name)
        kb_dir = Path(info["path"])

        # 删除文件
        import shutil
        if kb_dir.exists():
            shutil.rmtree(kb_dir)

        self._save_registry()
        return True

    def kb_exists(self, name: str) -> bool:
        """检查知识库是否存在。"""
        return name in self._registry

    def get_or_create_kb(self, name: str) -> KnowledgeBase:
        """获取或创建知识库。"""
        kb = self.get_kb(name)
        if kb is None:
            kb = self.create_kb(name)
        return kb

    def cleanup_orphaned_reindex_collections(self) -> None:
        """清理残留的临时 reindex 集合目录。"""
        for kb_name in self._registry:
            kb_dir = Path(self._registry[kb_name]["path"])
            tmp_dir = kb_dir / "chroma_db_reindex_tmp"
            if tmp_dir.exists():
                import shutil
                shutil.rmtree(tmp_dir)
                logger.info("清理残留 reindex 临时目录: %s", tmp_dir)


class DocumentStore:
    """文档存储层。

    管理单个知识库下的文档文件存储和 FTS5 全文索引。
    每个知识库有独立的 FTS5 SQLite 数据库。
    """

    def __init__(self, kb_name: str, kb_dir: str = ""):
        self.kb_name = kb_name
        self.kb_dir = Path(kb_dir) if kb_dir else Path("data/rag") / kb_name
        self.documents_dir = self.kb_dir / "documents"
        self.fts_db_path = self.kb_dir / "fts_index.db"

    def initialize(self) -> None:
        """初始化存储目录和 FTS5 索引。"""
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self._init_fts_db()

    def _init_fts_db(self) -> None:
        """初始化 FTS5 全文索引数据库。

        复用记忆系统的 unicode61 分词器策略，支持中英文混合检索。
        """
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(
                    chunk_id,
                    doc_id,
                    content,
                    metadata_json,
                    tokenize='unicode61'
                )
            """)
            # 文档元数据表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    kb_name TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    parser_type TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'importing',
                    status_message TEXT DEFAULT '',
                    chunk_count INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Chunk 元数据表（内容存储在 FTS5，额外信息在此）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    kb_name TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    content_hash TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}',
                    prev_chunk_id TEXT DEFAULT '',
                    next_chunk_id TEXT DEFAULT '',
                    FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
                )
            """)
            # Ingestion checkpoint 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_checkpoint (
                    doc_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    batch_index INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (doc_id, chunk_id)
                )
            """)
            # 嵌入维度元数据
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kb_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()

    # ========================================================================
    # 文档 CRUD
    # ========================================================================

    def insert_document(self, doc: Document) -> None:
        """插入文档记录。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO documents
                   (doc_id, kb_name, filename, file_type, parser_type,
                    content_hash, status, status_message, chunk_count,
                    total_tokens, metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc.id, doc.kb_name, doc.filename, doc.file_type,
                    doc.parser_type.value, doc.content_hash,
                    doc.status.value, doc.status_message,
                    doc.chunk_count, doc.total_tokens,
                    json.dumps(doc.metadata, ensure_ascii=False),
                    doc.created_at.isoformat(), doc.updated_at.isoformat(),
                ),
            )
            conn.commit()

    def update_document_status(self, doc_id: str, status: DocumentStatus,
                               status_message: str = "") -> None:
        """更新文档状态。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.execute(
                """UPDATE documents SET status = ?, status_message = ?,
                   updated_at = ? WHERE doc_id = ?""",
                (status.value, status_message, datetime.now().isoformat(), doc_id),
            )
            conn.commit()

    def update_document_stats(self, doc_id: str, chunk_count: int,
                              total_tokens: int) -> None:
        """更新文档统计信息。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.execute(
                """UPDATE documents SET chunk_count = ?, total_tokens = ?,
                   updated_at = ? WHERE doc_id = ?""",
                (chunk_count, total_tokens, datetime.now().isoformat(), doc_id),
            )
            conn.commit()

    def get_document(self, doc_id: str) -> Document | None:
        """获取文档信息。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()

        if row is None:
            return None

        from graph_agent.rag.rag_types import ParserType
        return Document(
            id=row["doc_id"],
            kb_name=row["kb_name"],
            filename=row["filename"],
            file_type=row["file_type"],
            parser_type=ParserType(row["parser_type"]),
            content_hash=row["content_hash"],
            status=DocumentStatus(row["status"]),
            status_message=row["status_message"] or "",
            chunk_count=row["chunk_count"],
            total_tokens=row["total_tokens"],
            metadata=json.loads(row["metadata_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_document_by_hash(self, content_hash: str) -> Document | None:
        """按内容哈希查找文档（去重用）。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT doc_id FROM documents WHERE content_hash = ? AND status != 'deleted'",
                (content_hash,),
            ).fetchone()

        if row is None:
            return None
        return self.get_document(row["doc_id"])

    def get_document_by_filename(self, filename: str) -> Document | None:
        """按文件名模糊匹配查找文档（精确子串匹配，case-insensitive）。

        多匹配时返回最近更新的。
        """
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT doc_id FROM documents
                   WHERE LOWER(filename) LIKE ? AND status != 'deleted'
                   ORDER BY updated_at DESC""",
                (f"%{filename.lower()}%",),
            ).fetchall()

        if not rows:
            return None
        return self.get_document(rows[0]["doc_id"])

    def get_document_content(self, doc_id: str) -> str | None:
        """获取文档的完整内容（所有 chunk 按顺序拼接）。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            rows = conn.execute(
                """SELECT c.content FROM chunks_fts f
                   JOIN chunks c ON f.chunk_id = c.chunk_id
                   WHERE c.doc_id = ?
                   ORDER BY c.chunk_index""",
                (doc_id,),
            ).fetchall()

        if not rows:
            return None
        return "\n\n".join(row[0] for row in rows)

    def list_documents(self, status_filter: str | None = None,
                       limit: int = 20, offset: int = 0) -> dict:
        """列出知识库中的文档。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.row_factory = sqlite3.Row

            if status_filter:
                total = conn.execute(
                    "SELECT COUNT(*) FROM documents WHERE status = ? AND status != 'deleted'",
                    (status_filter,),
                ).fetchone()[0]
                rows = conn.execute(
                    """SELECT * FROM documents
                       WHERE status = ? AND status != 'deleted'
                       ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                    (status_filter, limit, offset),
                ).fetchall()
            else:
                total = conn.execute(
                    "SELECT COUNT(*) FROM documents WHERE status != 'deleted'",
                ).fetchone()[0]
                rows = conn.execute(
                    """SELECT * FROM documents
                       WHERE status != 'deleted'
                       ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()

        documents = []
        for row in rows:
            documents.append({
                "doc_id": row["doc_id"],
                "filename": row["filename"],
                "status": row["status"],
                "chunk_count": row["chunk_count"],
                "total_tokens": row["total_tokens"],
                "file_type": row["file_type"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })

        return {
            "kb_name": self.kb_name,
            "total_count": total,
            "returned_count": len(documents),
            "offset": offset,
            "documents": documents,
        }

    def delete_document(self, doc_id: str) -> bool:
        """软删除文档及其 chunks。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            # 获取 chunk IDs 用于 FTS 清理
            chunk_ids = conn.execute(
                "SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,)
            ).fetchall()

            # 标记文档为 deleted
            conn.execute(
                """UPDATE documents SET status = ?, updated_at = ?
                   WHERE doc_id = ?""",
                (DocumentStatus.DELETED.value, datetime.now().isoformat(), doc_id),
            )

            # 删除 chunks
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

            # 删除 FTS 条目
            for (chunk_id,) in chunk_ids:
                conn.execute(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,)
                )

            # 删除 checkpoint
            conn.execute(
                "DELETE FROM ingestion_checkpoint WHERE doc_id = ?", (doc_id,)
            )

            conn.commit()
        return True

    # ========================================================================
    # Chunk CRUD + FTS5 索引
    # ========================================================================

    def insert_chunks(self, chunks: list[Chunk]) -> None:
        """批量插入 chunks 及其 FTS5 索引。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            for chunk in chunks:
                # FTS5 索引
                conn.execute(
                    """INSERT OR REPLACE INTO chunks_fts
                       (chunk_id, doc_id, content, metadata_json)
                       VALUES (?, ?, ?, ?)""",
                    (
                        chunk.id, chunk.doc_id, chunk.content,
                        json.dumps(chunk.metadata, ensure_ascii=False),
                    ),
                )
                # Chunk 元数据
                prev_id = chunk.metadata.get("prev_chunk_id", "")
                next_id = chunk.metadata.get("next_chunk_id", "")
                conn.execute(
                    """INSERT OR REPLACE INTO chunks
                       (chunk_id, doc_id, kb_name, chunk_index, token_count,
                        content_hash, metadata_json, prev_chunk_id, next_chunk_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.id, chunk.doc_id, chunk.kb_name,
                        chunk.chunk_index, chunk.token_count,
                        chunk.content_hash,
                        json.dumps(chunk.metadata, ensure_ascii=False),
                        prev_id, next_id,
                    ),
                )
            conn.commit()

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """获取单个 chunk。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT c.*, f.content FROM chunks c
                   JOIN chunks_fts f ON c.chunk_id = f.chunk_id
                   WHERE c.chunk_id = ?""",
                (chunk_id,),
            ).fetchone()

        if row is None:
            return None

        return Chunk(
            id=row["chunk_id"],
            doc_id=row["doc_id"],
            kb_name=row["kb_name"],
            content=row["content"],
            chunk_index=row["chunk_index"],
            token_count=row["token_count"],
            content_hash=row["content_hash"],
            metadata=json.loads(row["metadata_json"]),
        )

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        """批量获取 chunks。"""
        chunks = []
        for cid in chunk_ids:
            chunk = self.get_chunk(cid)
            if chunk:
                chunks.append(chunk)
        return chunks

    def get_chunks_by_doc_id(self, doc_id: str) -> list[Chunk]:
        """获取文档的所有 chunks（按序号排序）。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT c.*, f.content FROM chunks c
                   JOIN chunks_fts f ON c.chunk_id = f.chunk_id
                   WHERE c.doc_id = ?
                   ORDER BY c.chunk_index""",
                (doc_id,),
            ).fetchall()

        chunks = []
        for row in rows:
            chunks.append(Chunk(
                id=row["chunk_id"],
                doc_id=row["doc_id"],
                kb_name=row["kb_name"],
                content=row["content"],
                chunk_index=row["chunk_index"],
                token_count=row["token_count"],
                content_hash=row["content_hash"],
                metadata=json.loads(row["metadata_json"]),
            ))
        return chunks

    def get_all_chunk_ids(self) -> list[str]:
        """获取知识库中所有非删除文档的 chunk ID 列表。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            rows = conn.execute(
                """SELECT c.chunk_id FROM chunks c
                   JOIN documents d ON c.doc_id = d.doc_id
                   WHERE d.status != 'deleted'""",
            ).fetchall()
        return [row[0] for row in rows]

    def delete_chunks_by_doc_id(self, doc_id: str) -> int:
        """删除文档的所有 chunks（FTS + 元数据）。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            chunk_ids = conn.execute(
                "SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,)
            ).fetchall()
            count = len(chunk_ids)
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            for (chunk_id,) in chunk_ids:
                conn.execute(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,)
                )
            conn.commit()
        return count

    # ========================================================================
    # FTS5 关键词检索
    # ========================================================================

    def search_fts(self, query: str, top_k: int = 20,
                   metadata_filter: dict | None = None) -> list[tuple[Chunk, float]]:
        """FTS5 关键词检索。

        复用记忆系统的查询构造策略：
        - unicode61 分词器（中英文混合）
        - 特殊字符转义
        - 无结果时降级为 LIKE 模糊匹配
        """
        # 转义 FTS5 特殊字符
        safe_query = self._escape_fts_query(query)

        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """SELECT f.chunk_id, f.content, f.metadata_json,
                              c.doc_id, c.kb_name, c.chunk_index, c.token_count,
                              c.content_hash,
                              rank AS score
                       FROM chunks_fts f
                       JOIN chunks c ON f.chunk_id = c.chunk_id
                       JOIN documents d ON c.doc_id = d.doc_id
                       WHERE chunks_fts MATCH ? AND d.status = 'ready'
                       ORDER BY rank
                       LIMIT ?""",
                    (safe_query, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                # FTS5 MATCH 失败时降级为 LIKE
                rows = conn.execute(
                    """SELECT f.chunk_id, f.content, f.metadata_json,
                              c.doc_id, c.kb_name, c.chunk_index, c.token_count,
                              c.content_hash,
                              1.0 AS score
                       FROM chunks_fts f
                       JOIN chunks c ON f.chunk_id = c.chunk_id
                       JOIN documents d ON c.doc_id = d.doc_id
                       WHERE f.content LIKE ? AND d.status = 'ready'
                       LIMIT ?""",
                    (f"%{query}%", top_k),
                ).fetchall()

        results = []
        for row in rows:
            chunk = Chunk(
                id=row["chunk_id"],
                doc_id=row["doc_id"],
                kb_name=row["kb_name"],
                content=row["content"],
                chunk_index=row["chunk_index"],
                token_count=row["token_count"],
                content_hash=row["content_hash"],
                metadata=json.loads(row["metadata_json"]),
            )
            results.append((chunk, float(row["score"])))

        # 应用元数据过滤
        if metadata_filter:
            results = [
                (chunk, score) for chunk, score in results
                if self._match_metadata_filter(chunk.metadata, metadata_filter)
            ]

        return results

    @staticmethod
    def _escape_fts_query(query: str) -> str:
        """转义 FTS5 特殊字符。"""
        special_chars = r'*"()^!:{}[]'
        safe = query
        for char in special_chars:
            safe = safe.replace(char, f'"{char}"')
        return safe

    @staticmethod
    def _match_metadata_filter(metadata: dict, filter_dict: dict) -> bool:
        """检查 metadata 是否匹配过滤条件（精确匹配）。"""
        for key, value in filter_dict.items():
            if metadata.get(key) != value:
                return False
        return True

    # ========================================================================
    # Ingestion Checkpoint
    # ========================================================================

    def get_pending_chunks(self, doc_id: str) -> list[str]:
        """获取文档中尚未完成嵌入的 chunk ID 列表。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            rows = conn.execute(
                """SELECT c.chunk_id FROM chunks c
                   LEFT JOIN ingestion_checkpoint ic
                     ON c.chunk_id = ic.chunk_id AND c.doc_id = ic.doc_id
                   WHERE c.doc_id = ? AND ic.chunk_id IS NULL
                   ORDER BY c.chunk_index""",
                (doc_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def mark_chunk_embedded(self, doc_id: str, chunk_id: str,
                            batch_index: int = 0) -> None:
        """标记 chunk 的嵌入已完成。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO ingestion_checkpoint
                   (doc_id, chunk_id, status, batch_index, created_at)
                   VALUES (?, ?, 'embedded', ?, ?)""",
                (doc_id, chunk_id, batch_index, datetime.now().isoformat()),
            )
            conn.commit()

    def clear_checkpoint(self, doc_id: str) -> None:
        """清理文档的 checkpoint 记录。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.execute(
                "DELETE FROM ingestion_checkpoint WHERE doc_id = ?", (doc_id,)
            )
            conn.commit()

    # ========================================================================
    # 元数据 / 统计
    # ========================================================================

    def set_embedding_dimension(self, dimension: int) -> None:
        """记录知识库的嵌入向量维度。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO kb_metadata (key, value)
                   VALUES ('embedding_dimension', ?)""",
                (str(dimension),),
            )
            conn.commit()

    def get_embedding_dimension(self) -> int:
        """获取记录的嵌入向量维度。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM kb_metadata WHERE key = 'embedding_dimension'",
            ).fetchone()
        return int(row[0]) if row else 0

    def get_stats(self) -> dict:
        """获取知识库统计信息。"""
        with sqlite3.connect(str(self.fts_db_path)) as conn:
            doc_count = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status != 'deleted'",
            ).fetchone()[0]
            ready_doc_count = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status = 'ready'",
            ).fetchone()[0]
            chunk_count = conn.execute(
                """SELECT COUNT(*) FROM chunks c
                   JOIN documents d ON c.doc_id = d.doc_id
                   WHERE d.status != 'deleted'""",
            ).fetchone()[0]
            dimension = self.get_embedding_dimension()

        return {
            "document_count": doc_count,
            "ready_document_count": ready_doc_count,
            "chunk_count": chunk_count,
            "embedding_dimension": dimension,
        }
