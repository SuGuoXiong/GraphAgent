"""文档导入流水线。

编排 解析 -> 分块 -> 嵌入 -> 索引 的完整流程，
采用两阶段设计（Phase A 确定性离线 / Phase B 网络依赖可续跑），
支持断点续跑、取消、重导入和索引一致性校验。
"""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from graph_agent.rag.rag_types import (
    Chunk,
    ChunkStrategy,
    Document,
    DocumentStatus,
    EmbedderType,
    IngestionConfig,
    IntegrityReport,
    ParserType,
)
from graph_agent.rag.document_store import DocumentStore
from graph_agent.rag.rag_config import RAGConfig, KnowledgeBaseConfig
from graph_agent.rag.parser import create_default_registry, ParserRegistry

logger = logging.getLogger(__name__)


# ============================================================================
# 工具函数
# ============================================================================

def _generate_id() -> str:
    """生成带时间戳的唯一 ID。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{uuid.uuid4().hex[:8]}"


def _compute_hash(content: str) -> str:
    """计算内容的 SHA256 哈希。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ============================================================================
# CancellationToken
# ============================================================================

@dataclass
class CancellationToken:
    """取消令牌。

    用于在长时间运行的导入任务中传递取消信号。
    导入流水线在关键检查点（如每个 batch 完成后）检查此令牌。

    使用方式：
        token = CancellationToken()
        # 在另一个线程/任务中：
        token.cancel()
        # 在流水线中：
        if token.is_cancelled():
            raise IngestionCancelledError(...)
    """

    cancelled: bool = False

    def cancel(self) -> None:
        """设置取消信号。"""
        self.cancelled = True

    def is_cancelled(self) -> bool:
        """检查是否已被取消。"""
        return self.cancelled


class IngestionCancelledError(Exception):
    """导入被取消时抛出的异常。"""
    pass


# ============================================================================
# 嵌入缓存
# ============================================================================

class _EmbeddingCache:
    """嵌入向量缓存。

    双层缓存结构：
    - 内存 dict：快速命中
    - SQLite DB：跨进程持久化

    缓存键为文本内容的 SHA256 哈希值。
    """

    def __init__(self, cache_db_path: str = "data/rag/.ingestion_embed_cache.db"):
        self._memory_cache: dict[str, list[float]] = {}
        self._db_path = Path(cache_db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化缓存数据库。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embed_cache (
                    content_hash TEXT PRIMARY KEY,
                    embedding_json TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_embed_cache_model
                ON embed_cache(model)
            """)
            conn.commit()

    def get(self, content_hash: str, model: str = "") -> list[float] | None:
        """获取缓存的嵌入向量。

        Args:
            content_hash: 文本内容的 SHA256 哈希。
            model: 嵌入模型名称（区分不同模型的缓存）。

        Returns:
            嵌入向量列表，未缓存时返回 None。
        """
        # 内存缓存优先
        cache_key = f"{model}:{content_hash}"
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        # 查询 SQLite
        with sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute(
                "SELECT embedding_json FROM embed_cache WHERE content_hash = ? AND model = ?",
                (content_hash, model),
            ).fetchone()

        if row:
            embedding = json.loads(row[0])
            self._memory_cache[cache_key] = embedding
            return embedding

        return None

    def set(self, content_hash: str, embedding: list[float], model: str = "") -> None:
        """缓存嵌入向量。

        Args:
            content_hash: 文本内容的 SHA256 哈希。
            embedding: 嵌入向量。
            model: 嵌入模型名称。
        """
        cache_key = f"{model}:{content_hash}"
        self._memory_cache[cache_key] = embedding

        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO embed_cache
                   (content_hash, embedding_json, model, created_at)
                   VALUES (?, ?, ?, ?)""",
                (content_hash, json.dumps(embedding), model, datetime.now().isoformat()),
            )
            conn.commit()

    def get_batch(
        self, content_hashes: list[str], model: str = ""
    ) -> tuple[dict[str, list[float]], list[str]]:
        """批量获取缓存。

        Args:
            content_hashes: 文本哈希列表。
            model: 嵌入模型名称。

        Returns:
            (命中映射, 未命中哈希列表) 元组。
        """
        hits: dict[str, list[float]] = {}
        misses: list[str] = []

        for ch in content_hashes:
            emb = self.get(ch, model)
            if emb is not None:
                hits[ch] = emb
            else:
                misses.append(ch)

        return hits, misses

    def clear(self) -> None:
        """清空所有缓存。"""
        self._memory_cache.clear()
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM embed_cache")
            conn.commit()
        logger.info("已清空嵌入缓存")


# ============================================================================
# 嵌入客户端
# ============================================================================

class _EmbeddingClient:
    """嵌入向量生成客户端。

    支持多种后端：
    - OPENAI: OpenAI 兼容 API（含自定义 endpoint）
    - LOCAL: 本地 sentence-transformers
    - OLLAMA: Ollama 本地嵌入服务
    - ANTHROPIC: Anthropic Voyage API

    内置指数退避重试和速率限制。
    """

    _MAX_RETRIES = 3
    _BASE_DELAY = 1.0  # 基础退避延迟（秒）
    _MAX_DELAY = 30.0

    def __init__(self, cache: _EmbeddingCache | None = None):
        self._cache = cache or _EmbeddingCache()
        self._last_request_time = 0.0

    async def generate(
        self,
        texts: list[str],
        model: str = "text-embedding-3-small",
        provider: EmbedderType = EmbedderType.OPENAI,
        batch_size: int = 32,
        rate_limit: int = 10,
        cancellation_token: CancellationToken | None = None,
    ) -> list[list[float]]:
        """批量生成嵌入向量。

        Args:
            texts: 待嵌入的文本列表。
            model: 嵌入模型名称。
            provider: 嵌入服务提供商。
            batch_size: 单次 API 调用的最大文本数。
            rate_limit: 每秒最大请求数。
            cancellation_token: 可选的取消令牌。

        Returns:
            嵌入向量列表，顺序与 texts 对应。

        Raises:
            IngestionCancelledError: 任务被取消。
            RuntimeError: 嵌入生成失败。
        """
        if not texts:
            return []

        # 1. 查询缓存
        hashes = [_compute_hash(t) for t in texts]
        hits, misses_idx = self._cache.get_batch(hashes, model)

        # 2. 全部命中则直接返回
        if not misses_idx:
            return [hits[h] for h in hashes]

        # 3. 获取未命中的文本
        hash_to_text = {_compute_hash(t): t for t in texts}
        missed_texts = [hash_to_text[h] for h in misses_idx]

        # 4. 分批调用嵌入 API
        all_embeddings: dict[str, list[float]] = dict(hits)

        for i in range(0, len(missed_texts), batch_size):
            if cancellation_token and cancellation_token.is_cancelled():
                raise IngestionCancelledError("嵌入生成被取消")

            batch = missed_texts[i:i + batch_size]
            batch_hashes = misses_idx[i:i + batch_size]

            # 速率限制
            await self._rate_limit(rate_limit)

            # 带重试的 API 调用
            embeddings = await self._call_with_retry(batch, model, provider)

            # 缓存并记录结果
            for j, (text_hash, emb) in enumerate(zip(batch_hashes, embeddings)):
                all_embeddings[text_hash] = emb
                self._cache.set(text_hash, emb, model)

        # 5. 按原始顺序返回
        return [all_embeddings[h] for h in hashes]

    async def _call_with_retry(
        self,
        texts: list[str],
        model: str,
        provider: EmbedderType,
    ) -> list[list[float]]:
        """带指数退避重试的嵌入 API 调用。"""
        last_error = None

        for attempt in range(self._MAX_RETRIES):
            try:
                return await self._call_api(texts, model, provider)
            except Exception as e:
                last_error = e
                if attempt < self._MAX_RETRIES - 1:
                    delay = min(self._BASE_DELAY * (2 ** attempt), self._MAX_DELAY)
                    # 添加抖动
                    delay *= 0.5 + (hash(str(texts[0]) if texts else "") % 1000) / 1000.0
                    logger.warning(
                        "嵌入 API 调用失败 (attempt %d/%d): %s，%.1fs 后重试",
                        attempt + 1, self._MAX_RETRIES, e, delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(f"嵌入 API 调用失败（已重试 {self._MAX_RETRIES} 次）: {last_error}")

    async def _call_api(
        self,
        texts: list[str],
        model: str,
        provider: EmbedderType,
    ) -> list[list[float]]:
        """调用具体的嵌入 API。"""
        if provider == EmbedderType.OPENAI:
            return await self._call_openai(texts, model)
        elif provider == EmbedderType.OLLAMA:
            return await self._call_ollama(texts, model)
        elif provider == EmbedderType.LOCAL:
            return await self._call_local(texts, model)
        elif provider == EmbedderType.ANTHROPIC:
            return await self._call_anthropic(texts, model)
        else:
            raise ValueError(f"不支持的嵌入提供商: {provider.value}")

    async def _call_openai(self, texts: list[str], model: str) -> list[list[float]]:
        """调用 OpenAI 兼容嵌入 API。"""
        import aiohttp

        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "input": texts,
            "model": model,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/embeddings",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"OpenAI API 返回 {resp.status}: {body[:500]}")

                data = await resp.json()
                embeddings = [item["embedding"] for item in data["data"]]
                # 确保按输入顺序
                embeddings.sort(key=lambda x: x["index"])
                return [item["embedding"] for item in data["data"]]

    async def _call_ollama(self, texts: list[str], model: str) -> list[list[float]]:
        """调用 Ollama 嵌入 API。"""
        import aiohttp

        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

        embeddings: list[list[float]] = []
        async with aiohttp.ClientSession() as session:
            for text in texts:
                payload = {"model": model, "prompt": text}
                async with session.post(
                    f"{base_url}/api/embeddings",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise RuntimeError(f"Ollama API 返回 {resp.status}: {body[:500]}")
                    data = await resp.json()
                    embeddings.append(data["embedding"])
        return embeddings

    async def _call_local(self, texts: list[str], model: str) -> list[list[float]]:
        """调用本地 sentence-transformers 模型。

        通过 asyncio.to_thread 在线程池中运行同步推理。
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "本地嵌入需要安装 sentence-transformers: "
                "pip install sentence-transformers"
            )

        # 在线程池中运行同步的 encode（避免阻塞事件循环）
        def _encode():
            st_model = SentenceTransformer(model)
            result = st_model.encode(texts, normalize_embeddings=True)
            return [vec.tolist() for vec in result]

        return await asyncio.to_thread(_encode)

    async def _call_anthropic(self, texts: list[str], model: str) -> list[list[float]]:
        """调用 Anthropic Voyage 嵌入 API。

        Voyage API 的请求格式：
        POST https://api.anthropic.com/v1/embeddings
        或使用 voyageai 包。
        """
        import aiohttp

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("未设置 ANTHROPIC_API_KEY 环境变量")

        # 先尝试 voyageai 包
        try:
            import voyageai
            voyage_client = voyageai.Client(api_key=api_key)

            def _voyage_encode():
                result = voyage_client.embed(texts, model=model)
                return result.embeddings

            return await asyncio.to_thread(_voyage_encode)
        except ImportError:
            pass

        # 回退到直接 HTTP 调用
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
        }
        payload = {
            "input": texts,
            "model": model,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/embeddings",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Anthropic API 返回 {resp.status}: {body[:500]}")
                data = await resp.json()
                return data["embeddings"]

    async def _rate_limit(self, rate_limit: int) -> None:
        """简单的速率限制（令牌桶近似）。

        Args:
            rate_limit: 每秒最大请求数。
        """
        if rate_limit <= 0:
            return

        min_interval = 1.0 / rate_limit
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_request_time = time.monotonic()


# ============================================================================
# IngestionPipeline
# ============================================================================

class IngestionPipeline:
    """文档导入流水线。

    编排 解析 -> 分块 -> 嵌入 -> 向量索引 的完整流程。

    两阶段设计：
    ┌─────────────────────────────────────────────────────────┐
    │ Phase A（确定性，无网络依赖）                            │
    │   1. 解析文档 -> 提取文本 + 元数据                       │
    │   2. 文本分块 -> 生成 Chunk 列表                        │
    │   3. 持久化 Document + Chunks 到 DocumentStore           │
    │      （此时 embedding 字段为空）                         │
    ├─────────────────────────────────────────────────────────┤
    │ Phase B（网络依赖，可续跑）                              │
    │   4. 查询 checkpoint DB，跳过已完成的 chunk              │
    │   5. 批量生成嵌入向量（带缓存 + 指数退避重试）           │
    │   6. 逐批写入 VectorStore + 更新 checkpoint              │
    │   7. 所有 batch 完成后：标记 document 为 READY           │
    └─────────────────────────────────────────────────────────┘

    使用方式：
        pipeline = IngestionPipeline(rag_config)
        docs = await pipeline.ingest(
            ["docs/api.md", "docs/guide.md"],
            IngestionConfig(kb_name="my_kb"),
        )
    """

    def __init__(self, rag_config: RAGConfig | None = None,
                 embedder: "BaseEmbedder | None" = None):
        """初始化导入流水线。

        Args:
            rag_config: 全局 RAG 配置，为 None 时使用默认配置。
            embedder: 外部嵌入器。提供后导入流水线使用该嵌入器进行文档向量化，
                      与 RAGService 共享同一嵌入实例（含缓存层）。
                      为 None 时使用内置 _EmbeddingClient（独立嵌入路径）。
        """
        self.rag_config = rag_config or RAGConfig()
        self._external_embedder = embedder
        # 内置嵌入客户端（仅在无外部 embedder 时使用）
        if embedder is None:
            ingestion_cache_db = str(Path(self.rag_config.embedding.cache_db).with_name(".ingestion_embed_cache.db"))
            self._embedding_client = _EmbeddingClient(
                cache=_EmbeddingCache(ingestion_cache_db)
            )
        else:
            self._embedding_client = None
        self._parser_registry: ParserRegistry | None = None

    @property
    def parser_registry(self) -> ParserRegistry:
        """懒加载解析器注册表。"""
        if self._parser_registry is None:
            self._parser_registry = create_default_registry()
        return self._parser_registry

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def ingest(
        self,
        file_paths: list[str],
        config: IngestionConfig,
        progress_callback: Callable[[str, float, dict], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> list[Document]:
        """批量导入多个文档。

        Args:
            file_paths: 文件路径列表。
            config: 导入配置（kb_name 必填）。
            progress_callback: 进度回调 (phase, progress, info)。
            cancellation_token: 可选的取消令牌。

        Returns:
            导入成功的 Document 列表。

        Raises:
            IngestionCancelledError: 导入被取消。
        """
        if not file_paths:
            return []

        documents: list[Document] = []
        total = len(file_paths)

        for idx, file_path in enumerate(file_paths):
            if cancellation_token and cancellation_token.is_cancelled():
                logger.info("批量导入被取消 (已完成 %d/%d)", idx, total)
                raise IngestionCancelledError("批量导入被取消")

            try:
                doc = await self.ingest_single(
                    file_path, config, cancellation_token,
                )
                documents.append(doc)
            except IngestionCancelledError:
                raise
            except Exception as e:
                logger.error("导入文件失败 '%s': %s", file_path, e)
                # 创建一个错误状态的文档记录
                error_doc = self._create_error_document(file_path, config, str(e))
                documents.append(error_doc)

            if progress_callback:
                progress_callback(
                    "batch",
                    (idx + 1) / total,
                    {"current": idx + 1, "total": total, "file": file_path},
                )

        return documents

    async def ingest_single(
        self,
        file_path: str,
        config: IngestionConfig,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[str, float, dict], None] | None = None,
    ) -> Document:
        """导入单个文档。

        完整执行 Phase A + Phase B。

        Args:
            file_path: 文件路径。
            config: 导入配置。
            cancellation_token: 可选的取消令牌。
            progress_callback: 进度回调 (phase, progress, info)。

        Returns:
            导入完成的 Document 对象。

        Raises:
            FileNotFoundError: 文件不存在。
            ValueError: 文件类型不支持。
            IngestionCancelledError: 导入被取消。
        """
        # 校验文件
        path_obj = Path(file_path).resolve()
        if not path_obj.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        if not self._is_supported(path_obj):
            raise ValueError(
                f"不支持的文件类型: '{path_obj.suffix}'，"
                f"支持: {self.rag_config.ingestion.supported_extensions}"
            )

        # ── Phase A: 解析 + 分块 + 持久化 ──────────────────────────────

        # 1. 解析文档
        doc_text, parser_metadata = self._parse_document(
            str(path_obj), config.parser_type,
        )

        # 2. 去重检查
        content_hash = _compute_hash(doc_text)
        if config.skip_duplicates:
            doc_store = self._get_doc_store(config.kb_name)
            existing = doc_store.get_document_by_hash(content_hash)
            if existing is not None:
                logger.info(
                    "跳过重复文档 '%s'（hash=%s，已有 doc_id=%s）",
                    path_obj.name, content_hash[:16], existing.id,
                )
                return existing

        # 3. 选择分块策略
        chunk_strategy = self._resolve_chunk_strategy(config)
        chunk_size = config.chunk_size or self.rag_config.ingestion.default_chunk_size
        chunk_overlap = config.chunk_overlap or self.rag_config.ingestion.default_chunk_overlap

        # 4. 创建文档 ID 和元数据
        doc_id = _generate_id()
        file_type = path_obj.suffix.lstrip(".").lower()
        parser_type = self._resolve_parser_type(config.parser_type, file_type)

        # 合并元数据
        merged_metadata: dict = {}
        merged_metadata.update(parser_metadata)
        merged_metadata.update(config.metadata)
        merged_metadata["source_file"] = str(path_obj)

        # 5. 创建 Document（状态: IMPORTING）
        document = Document(
            id=doc_id,
            kb_name=config.kb_name,
            filename=path_obj.name,
            file_type=file_type,
            parser_type=parser_type,
            content_hash=content_hash,
            status=DocumentStatus.IMPORTING,
            metadata=merged_metadata,
        )

        # 6. 分块
        chunk_metadata = {
            "doc_id": doc_id,
            "kb_name": config.kb_name,
            **parser_metadata,
        }
        chunks = self._chunk_text(doc_text, chunk_metadata, chunk_strategy, chunk_size, chunk_overlap)

        if not chunks:
            logger.warning("文档 '%s' 分块结果为空", path_obj.name)
            document.status = DocumentStatus.ERROR
            document.status_message = "分块结果为空"
            doc_store = self._get_doc_store(config.kb_name)
            doc_store.initialize()
            doc_store.insert_document(document)
            return document

        # 更新文档统计
        document.chunk_count = len(chunks)
        document.total_tokens = sum(c.token_count for c in chunks)

        # 7. 持久化到 DocumentStore (Phase A 完成)
        doc_store = self._get_doc_store(config.kb_name)
        doc_store.initialize()
        doc_store.insert_document(document)
        doc_store.insert_chunks(chunks)

        if progress_callback:
            progress_callback("phase_a_done", 0.3, {
                "doc_id": doc_id,
                "chunk_count": len(chunks),
                "total_tokens": document.total_tokens,
            })

        logger.info(
            "Phase A 完成: doc_id=%s, chunks=%d, tokens=%d",
            doc_id, len(chunks), document.total_tokens,
        )

        # ── Phase B: 嵌入 + 向量索引 + checkpoint ─────────────────────

        await self._run_phase_b(
            document=document,
            chunks=chunks,
            config=config,
            doc_store=doc_store,
            progress_callback=progress_callback,
            cancellation_token=cancellation_token,
        )

        return document

    async def resume_ingestion(
        self,
        doc_id: str,
        progress_callback: Callable[[str, float, dict], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> Document:
        """从上次 checkpoint 续跑导入。

        仅重新执行 Phase B（嵌入 + 索引），
        跳过已完成的分块。

        Args:
            doc_id: 要续跑的文档 ID。
            progress_callback: 进度回调。
            cancellation_token: 可选的取消令牌。

        Returns:
            续跑完成的 Document 对象。

        Raises:
            ValueError: 文档不存在。
            IngestionCancelledError: 导入被取消。
        """
        # 查找文档
        doc = self._find_document(doc_id)
        if doc is None:
            raise ValueError(f"文档不存在: {doc_id}")

        # 仅允许从 ERROR 或 IMPORTING 状态续跑
        if doc.status not in (DocumentStatus.ERROR, DocumentStatus.IMPORTING):
            if doc.status == DocumentStatus.READY:
                logger.info("文档 %s 已处于 READY 状态，无需续跑", doc_id)
                return doc
            raise ValueError(
                f"无法从 '{doc.status.value}' 状态续跑，"
                f"仅支持 ERROR 或 IMPORTING 状态"
            )

        # 更新状态为 IMPORTING
        doc_store = self._get_doc_store(doc.kb_name)
        doc_store.update_document_status(doc_id, DocumentStatus.IMPORTING)
        doc.status = DocumentStatus.IMPORTING

        # 获取所有 chunks
        chunks = doc_store.get_chunks_by_doc_id(doc_id)

        # 构建 IngestionConfig
        config = IngestionConfig(kb_name=doc.kb_name)

        # 执行 Phase B
        await self._run_phase_b(
            document=doc,
            chunks=chunks,
            config=config,
            doc_store=doc_store,
            progress_callback=progress_callback,
            cancellation_token=cancellation_token,
        )

        return doc

    async def delete_document(self, doc_id: str) -> None:
        """删除文档及其所有关联数据。

        清理范围：
        - DocumentStore 中的文档记录（软删除）
        - DocumentStore 中的 chunks 记录
        - FTS5 索引
        - ChromaDB 向量
        - Checkpoint 记录

        Args:
            doc_id: 要删除的文档 ID。

        Raises:
            ValueError: 文档不存在。
        """
        doc = self._find_document(doc_id)
        if doc is None:
            raise ValueError(f"文档不存在: {doc_id}")

        doc_store = self._get_doc_store(doc.kb_name)

        # 1. 清理向量存储
        vector_store = self._get_vector_store(doc.kb_name)
        deleted_count = await vector_store.delete_by_doc_id(doc.kb_name, doc_id)
        logger.info("从向量存储删除 %d 条向量 (doc_id=%s)", deleted_count, doc_id)

        # 2. 清理 DocumentStore（FTS + chunks + checkpoint + 文档软删除）
        doc_store.delete_document(doc_id)

        logger.info("文档已删除: doc_id=%s", doc_id)

    async def update_document(
        self,
        doc_id: str,
        file_path: str,
        cancellation_token: CancellationToken | None = None,
    ) -> Document:
        """更新文档（检测变更后重新导入）。

        流程：
        1. 读取新文件内容，计算 content_hash
        2. 比较与原文档的 hash
        3. 若未变更：返回原文档（不操作）
        4. 若有变更：清理旧数据 -> 重新导入（Phase A + B）

        Args:
            doc_id: 要更新的文档 ID。
            file_path: 新文件路径。
            cancellation_token: 可选的取消令牌。

        Returns:
            更新后的 Document 对象。

        Raises:
            ValueError: 文档不存在或文件不存在。
        """
        doc = self._find_document(doc_id)
        if doc is None:
            raise ValueError(f"文档不存在: {doc_id}")

        path_obj = Path(file_path).resolve()
        if not path_obj.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 读取新文件并计算 hash
        doc_text, _ = self._parse_document(str(path_obj), doc.parser_type)
        new_hash = _compute_hash(doc_text)

        if new_hash == doc.content_hash:
            logger.info("文档内容未变更，跳过更新: %s (hash=%s)", doc_id, new_hash[:16])
            return doc

        logger.info(
            "文档内容已变更: %s (old_hash=%s, new_hash=%s)",
            doc_id, doc.content_hash[:16], new_hash[:16],
        )

        # 标记为 REIMPORTING
        doc_store = self._get_doc_store(doc.kb_name)
        doc_store.update_document_status(doc_id, DocumentStatus.REIMPORTING)

        # 清理旧数据
        vector_store = self._get_vector_store(doc.kb_name)
        await vector_store.delete_by_doc_id(doc.kb_name, doc_id)
        doc_store.clear_checkpoint(doc_id)
        doc_store.delete_chunks_by_doc_id(doc_id)

        # 重新导入
        config = IngestionConfig(
            kb_name=doc.kb_name,
            parser_type=doc.parser_type,
            skip_duplicates=False,  # 更新时不跳过重复
        )

        return await self.ingest_single(file_path, config, cancellation_token)

    async def cancel_ingestion(self, doc_id: str) -> None:
        """取消正在进行的导入任务。

        保留 checkpoint 数据以便后续 resume_ingestion 续跑。
        将文档状态设置为 ERROR。

        Args:
            doc_id: 要取消导入的文档 ID。

        Raises:
            ValueError: 文档不存在。
        """
        doc = self._find_document(doc_id)
        if doc is None:
            raise ValueError(f"文档不存在: {doc_id}")

        if doc.status not in (DocumentStatus.IMPORTING, DocumentStatus.REIMPORTING):
            logger.warning(
                "文档 %s 当前状态为 '%s'，不在导入中，跳过取消",
                doc_id, doc.status.value,
            )
            return

        doc_store = self._get_doc_store(doc.kb_name)
        doc_store.update_document_status(
            doc_id,
            DocumentStatus.ERROR,
            status_message="用户取消导入",
        )
        logger.info("导入已取消，checkpoint 已保留: doc_id=%s", doc_id)

    async def verify_index_integrity(self, kb_name: str) -> IntegrityReport:
        """校验知识库的索引一致性。

        比较 chunk_ids 在 DocumentStore（chunks 表）、FTS5 和 ChromaDB
        三者之间的一致性，报告缺失和孤立的条目。

        Args:
            kb_name: 知识库名称。

        Returns:
            IntegrityReport 对象，包含详细的一致性分析。
        """
        doc_store = self._get_doc_store(kb_name)

        # 1. 收集各来源的 chunk ID 集合
        # DocumentStore chunks 表（非删除文档的 chunk）
        docstore_chunk_ids: set[str] = set(doc_store.get_all_chunk_ids())

        # FTS5 索引
        fts_chunk_ids: set[str] = self._get_fts_chunk_ids(doc_store)

        # ChromaDB
        vector_store = self._get_vector_store(kb_name)
        chroma_chunk_ids: set[str] = await vector_store.get_chunk_ids(kb_name)

        # 2. 计算差异
        # 以 DocumentStore 为基准
        missing_in_fts = sorted(docstore_chunk_ids - fts_chunk_ids)
        missing_in_vector = sorted(docstore_chunk_ids - chroma_chunk_ids)

        # 孤立条目（存在于索引但不在 DocumentStore）
        orphaned_in_fts = sorted(fts_chunk_ids - docstore_chunk_ids)
        orphaned_in_vector = sorted(chroma_chunk_ids - docstore_chunk_ids)

        is_consistent = (
            len(missing_in_fts) == 0
            and len(missing_in_vector) == 0
            and len(orphaned_in_fts) == 0
            and len(orphaned_in_vector) == 0
        )

        report = IntegrityReport(
            kb_name=kb_name,
            is_consistent=is_consistent,
            total_chunks_in_docstore=len(docstore_chunk_ids),
            total_chunks_in_fts=len(fts_chunk_ids),
            total_chunks_in_vector=len(chroma_chunk_ids),
            missing_in_fts=missing_in_fts,
            missing_in_vector=missing_in_vector,
            orphaned_in_fts=orphaned_in_fts,
            orphaned_in_vector=orphaned_in_vector,
        )

        if is_consistent:
            logger.info(
                "索引一致性校验通过: kb_name=%s, total=%d chunks",
                kb_name, len(docstore_chunk_ids),
            )
        else:
            logger.warning(
                "索引不一致: kb_name=%s, missing_fts=%d, missing_vector=%d, "
                "orphaned_fts=%d, orphaned_vector=%d",
                kb_name,
                len(missing_in_fts), len(missing_in_vector),
                len(orphaned_in_fts), len(orphaned_in_vector),
            )

        return report

    # ── Phase A 辅助方法 ─────────────────────────────────────────────────

    def _parse_document(
        self,
        file_path: str,
        parser_type: ParserType = ParserType.AUTO,
    ) -> tuple[str, dict]:
        """解析文档，提取文本和元数据。

        Args:
            file_path: 文件路径。
            parser_type: 强制使用的解析器类型。

        Returns:
            (text_content, metadata) 元组。

        Raises:
            ValueError: 找不到合适的解析器。
        """
        parser = self.parser_registry.get_parser(file_path, parser_type)
        if parser is None:
            raise ValueError(f"找不到适合文件 '{file_path}' 的解析器")

        text, metadata = parser.parse(file_path)
        logger.debug(
            "解析完成: %s, 文本长度=%d, 解析器=%s",
            Path(file_path).name, len(text), parser.get_parser_name(),
        )
        return text, metadata

    def _chunk_text(
        self,
        text: str,
        base_metadata: dict,
        strategy: ChunkStrategy,
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[Chunk]:
        """对文本执行分块。

        Args:
            text: 待分块的文本。
            base_metadata: 基础元数据（需包含 doc_id 和 kb_name）。
            strategy: 分块策略。
            chunk_size: 目标分块大小（tokens）。
            chunk_overlap: 分块重叠（tokens）。

        Returns:
            Chunk 对象列表。
        """
        chunker = self._get_chunker(strategy, chunk_size, chunk_overlap)
        chunks = chunker.chunk(text, base_metadata)
        logger.info(
            "分块完成: 策略=%s, 分块数=%d, chunk_size=%d",
            strategy.value, len(chunks), chunk_size,
        )
        return chunks

    @staticmethod
    def _get_chunker(
        strategy: ChunkStrategy,
        chunk_size: int,
        chunk_overlap: int,
    ):
        """根据策略获取分块器实例。

        直接导入分块器模块（绕过可能损坏的 ChunkerRegistry）。

        Args:
            strategy: 分块策略。
            chunk_size: 分块大小。
            chunk_overlap: 重叠大小。

        Returns:
            BaseChunker 实例。

        Raises:
            ValueError: 不支持的分块策略。
        """
        from graph_agent.rag.chunking.token_chunker import TokenChunker
        from graph_agent.rag.chunking.recursive_chunker import RecursiveChunker

        if strategy == ChunkStrategy.TOKEN:
            return TokenChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        elif strategy == ChunkStrategy.RECURSIVE:
            return RecursiveChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        elif strategy == ChunkStrategy.SEMANTIC:
            # 语义分块器尚未实现，回退到递归分块
            logger.warning("语义分块尚未实现，回退到递归分块")
            return RecursiveChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        else:
            raise ValueError(f"不支持的分块策略: {strategy.value}")

    @staticmethod
    def _resolve_chunk_strategy(config: IngestionConfig) -> ChunkStrategy:
        """解析分块策略（配置优先于默认值）。"""
        if config.chunk_strategy is not None:
            return config.chunk_strategy
        return ChunkStrategy.RECURSIVE  # 默认递归分块

    @staticmethod
    def _resolve_parser_type(parser_type: ParserType, file_type: str) -> ParserType:
        """解析解析器类型。"""
        if parser_type != ParserType.AUTO:
            return parser_type
        # 根据扩展名推断
        if file_type in ("md", "markdown", "mdx"):
            return ParserType.MARKDOWN
        elif file_type in ("txt", "py", "js", "ts", "java", "go", "yaml", "yml", "json"):
            return ParserType.TEXT
        elif file_type == "pdf":
            return ParserType.PDF
        return ParserType.TEXT

    def _is_supported(self, path_obj: Path) -> bool:
        """检查文件扩展名是否受支持。"""
        ext = path_obj.suffix.lower()
        return ext in self.rag_config.ingestion.supported_extensions

    # ── Phase B ──────────────────────────────────────────────────────────

    async def _run_phase_b(
        self,
        document: Document,
        chunks: list[Chunk],
        config: IngestionConfig,
        doc_store: DocumentStore,
        progress_callback: Callable[[str, float, dict], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        """执行 Phase B：嵌入生成 + 向量索引 + checkpoint。

        从 DocumentStore checkpoint 中查询待处理的分块，
        跳过已完成的，批量生成嵌入并写入向量存储。

        Args:
            document: 文档对象。
            chunks: 所有分块列表。
            config: 导入配置。
            doc_store: 文档存储实例。
            progress_callback: 进度回调。
            cancellation_token: 取消令牌。

        Raises:
            IngestionCancelledError: 导入被取消。
        """
        doc_id = document.id
        kb_name = document.kb_name

        # 1. 查询 pending chunks（断点续跑支持）
        pending_ids = doc_store.get_pending_chunks(doc_id)

        if not pending_ids:
            # 所有 chunk 的嵌入已完成
            self._finalize_phase_b(document, doc_store, progress_callback)
            return

        # 构建 pending chunk 映射 (chunk_id -> Chunk)
        chunk_map: dict[str, Chunk] = {c.id: c for c in chunks}
        pending_chunks = [chunk_map[cid] for cid in pending_ids if cid in chunk_map]

        if not pending_chunks:
            # pending IDs 中有的不在 chunks 列表中（可能是数据不一致）
            logger.warning("pending chunk IDs 与 chunks 列表不匹配: doc_id=%s", doc_id)
            self._finalize_phase_b(document, doc_store, progress_callback)
            return

        # 2. 获取知识库的嵌入配置
        kb_config = self._load_kb_config(kb_name)
        embedder_type = kb_config.embedder_provider if kb_config else EmbedderType.OPENAI
        embedder_model = kb_config.embedder_model if kb_config else "text-embedding-3-small"

        embed_config = self.rag_config.embedding
        batch_size = embed_config.batch_size
        rate_limit = embed_config.rate_limit

        # 3. 确保向量集合存在
        # 先确定维度：取第一个有效 chunk 的 embedding 维度，或从 KB 元数据读取
        dimension = self._get_embedding_dimension(doc_store, pending_chunks)

        vector_store = self._get_vector_store(kb_name)
        if not await vector_store.collection_exists(kb_name):
            if dimension > 0:
                await vector_store.create_collection(kb_name, dimension)
            else:
                # 维度未知，将在首次嵌入后创建
                pass

        # 4. 分批次处理
        total_pending = len(pending_chunks)
        completed_count = 0
        batch_index = 0

        for i in range(0, total_pending, batch_size):
            if cancellation_token and cancellation_token.is_cancelled():
                logger.info("Phase B 被取消 (doc_id=%s, 已完成 %d/%d)", doc_id, completed_count, total_pending)
                doc_store.update_document_status(
                    doc_id, DocumentStatus.ERROR,
                    status_message="导入被取消",
                )
                raise IngestionCancelledError("Phase B 被取消")

            batch = pending_chunks[i:i + batch_size]

            # 跳过已有嵌入的分块（可能由前一次失败的部分完成导致）
            chunks_to_embed = []
            chunks_to_skip = []
            for chunk in batch:
                if chunk.embedding is not None:
                    chunks_to_skip.append(chunk)
                else:
                    chunks_to_embed.append(chunk)

            # 先写入已有嵌入的 chunk
            if chunks_to_skip:
                await vector_store.add_chunks(kb_name, chunks_to_skip)
                for c in chunks_to_skip:
                    doc_store.mark_chunk_embedded(doc_id, c.id, batch_index)

            # 生成嵌入
            if chunks_to_embed:
                texts = [c.content for c in chunks_to_embed]
                try:
                    if self._external_embedder is not None:
                        # 使用外部嵌入器（与 RAGService 共享，含 CacheEmbedder 缓存）
                        embeddings = self._external_embedder.embed(texts)
                    else:
                        # 使用内置嵌入客户端
                        embeddings = await self._embedding_client.generate(
                            texts=texts,
                            model=embedder_model,
                            provider=embedder_type,
                            batch_size=batch_size,
                            rate_limit=rate_limit,
                            cancellation_token=cancellation_token,
                        )
                except IngestionCancelledError:
                    raise
                except Exception as e:
                    logger.error("嵌入生成失败 (doc_id=%s, batch=%d): %s", doc_id, batch_index, e)
                    doc_store.update_document_status(
                        doc_id, DocumentStatus.ERROR,
                        status_message=f"嵌入生成失败: {e}",
                    )
                    raise

                # 设置嵌入向量
                for chunk, embedding in zip(chunks_to_embed, embeddings):
                    chunk.embedding = embedding

                # 首次嵌入后，确保向量集合存在
                if not await vector_store.collection_exists(kb_name):
                    actual_dim = len(embeddings[0])
                    await vector_store.create_collection(kb_name, actual_dim)
                    doc_store.set_embedding_dimension(actual_dim)

                # 写入向量存储
                await vector_store.add_chunks(kb_name, chunks_to_embed)

                # 更新 checkpoint
                for chunk in chunks_to_embed:
                    doc_store.mark_chunk_embedded(doc_id, chunk.id, batch_index)

            completed_count += len(batch)
            batch_index += 1

            if progress_callback:
                phase_b_progress = 0.3 + 0.65 * (completed_count / total_pending)
                progress_callback("embedding", phase_b_progress, {
                    "doc_id": doc_id,
                    "completed": completed_count,
                    "total": total_pending,
                })

            logger.debug(
                "Phase B batch %d 完成: doc_id=%s, %d/%d chunks",
                batch_index, doc_id, completed_count, total_pending,
            )

        # 5. 完成
        self._finalize_phase_b(document, doc_store, progress_callback)

    def _finalize_phase_b(
        self,
        document: Document,
        doc_store: DocumentStore,
        progress_callback: Callable[[str, float, dict], None] | None = None,
    ) -> None:
        """完成 Phase B：标记文档为 READY，更新统计。"""
        doc_store.update_document_status(document.id, DocumentStatus.READY)
        doc_store.update_document_stats(
            document.id, document.chunk_count, document.total_tokens,
        )
        document.status = DocumentStatus.READY

        if progress_callback:
            progress_callback("complete", 1.0, {
                "doc_id": document.id,
                "chunk_count": document.chunk_count,
            })

        logger.info(
            "Phase B 完成: doc_id=%s, chunks=%d, status=READY",
            document.id, document.chunk_count,
        )

    # ── 辅助方法 ─────────────────────────────────────────────────────────

    def _get_doc_store(self, kb_name: str) -> DocumentStore:
        """获取知识库对应的 DocumentStore 实例。"""
        kb_dir = Path(self.rag_config.vector_store.persist_dir) / kb_name
        return DocumentStore(kb_name, str(kb_dir))

    def _get_vector_store(self, kb_name: str):
        """获取知识库对应的向量存储实例。"""
        from graph_agent.rag.vector_store import VectorStoreRegistry
        return VectorStoreRegistry.get_or_create_store(
            kb_name=kb_name,
            backend=self.rag_config.vector_store.backend,
            persist_dir=self.rag_config.vector_store.persist_dir,
        )

    def _find_document(self, doc_id: str) -> Document | None:
        """在已知知识库中搜索文档。

        遍历注册的知识库查找指定 doc_id。

        Args:
            doc_id: 文档 ID。

        Returns:
            找到的 Document 对象，未找到返回 None。
        """
        from graph_agent.rag.document_store import KnowledgeBaseManager

        kb_manager = KnowledgeBaseManager(self.rag_config.vector_store.persist_dir)
        for kb_info in kb_manager.list_kbs():
            kb_name = kb_info["name"]
            doc_store = self._get_doc_store(kb_name)
            doc = doc_store.get_document(doc_id)
            if doc is not None:
                return doc
        return None

    def _load_kb_config(self, kb_name: str) -> KnowledgeBaseConfig | None:
        """加载知识库配置。"""
        kb_dir = Path(self.rag_config.vector_store.persist_dir) / kb_name
        config_path = kb_dir / "kb_config.yaml"
        if config_path.exists():
            return KnowledgeBaseConfig.from_yaml(config_path)
        return None

    @staticmethod
    def _get_embedding_dimension(
        doc_store: DocumentStore,
        chunks: list[Chunk],
    ) -> int:
        """获取嵌入向量维度。

        优先级：
        1. chunks 中第一个已有 embedding 的维度
        2. DocumentStore 元数据中记录的维度
        3. 返回 0（维度未知）

        Args:
            doc_store: 文档存储实例。
            chunks: 分块列表。

        Returns:
            嵌入向量维度，未知时返回 0。
        """
        for chunk in chunks:
            if chunk.embedding is not None and len(chunk.embedding) > 0:
                return len(chunk.embedding)

        return doc_store.get_embedding_dimension()

    @staticmethod
    def _get_fts_chunk_ids(doc_store: DocumentStore) -> set[str]:
        """从 FTS5 索引获取所有 chunk_id。"""
        with sqlite3.connect(str(doc_store.fts_db_path)) as conn:
            rows = conn.execute(
                "SELECT chunk_id FROM chunks_fts"
            ).fetchall()
        return {row[0] for row in rows}

    @staticmethod
    def _create_error_document(
        file_path: str,
        config: IngestionConfig,
        error_message: str,
    ) -> Document:
        """创建错误状态的文档记录。

        Args:
            file_path: 文件路径。
            config: 导入配置。
            error_message: 错误信息。

        Returns:
            错误状态的 Document 对象。
        """
        path_obj = Path(file_path)
        doc_id = _generate_id()

        return Document(
            id=doc_id,
            kb_name=config.kb_name,
            filename=path_obj.name,
            file_type=path_obj.suffix.lstrip(".").lower(),
            parser_type=ParserType.AUTO,
            content_hash="",
            status=DocumentStatus.ERROR,
            status_message=error_message,
            metadata={"source_file": str(path_obj)},
        )
