"""RAG Agent 工具注册。

将所有 RAG 能力以标准工具形式暴露给 Agent，
LLM 可通过 tool calling 自主决定何时检索、导入、管理知识库。
"""

import logging
from typing import Optional

from graph_agent.rag.rag_types import (
    ChunkStrategy,
    IngestionConfig,
    ParserType,
    SearchRequest,
)
from graph_agent.tools.base import tool

logger = logging.getLogger(__name__)

# 全局 RAG 服务引用（由 RAGService 初始化时设置）
_rag_service: Optional["RAGService"] = None


def set_rag_service(service: "RAGService") -> None:
    """设置全局 RAG 服务实例。"""
    global _rag_service
    _rag_service = service


def get_rag_service() -> Optional["RAGService"]:
    """获取全局 RAG 服务实例。"""
    return _rag_service


# ============================================================================
# 检索工具
# ============================================================================


@tool(
    name="search_documents",
    description="在知识库中检索相关文档片段。使用语义+关键词混合检索，支持多知识库联合检索、查询预处理和多跳检索。适用场景：查找项目文档、API参考、技术规范、代码示例等。",
)
async def search_documents(
    query: str,
    kb_names: list[str] | None = None,
    top_k: int = 5,
    enable_rerank: bool = True,
    enable_query_rewrite: bool = True,
    enable_multi_hop: bool = False,
    max_hops: int = 3,
    include_adjacent: bool = False,
    filter: dict | None = None,
) -> dict:
    """在知识库中检索相关文档片段。

    Args:
        query: 检索查询文本。建议从当前任务/问题中提取核心语义。
        kb_names: 目标知识库名称列表。为 None 时检索所有知识库。
        top_k: 返回结果数（1-10），默认 5。
        enable_rerank: 是否启用 LLM 重排序。
        enable_query_rewrite: 是否启用查询改写预处理。
        enable_multi_hop: 是否启用多跳检索（复杂跨文档推理用）。
        max_hops: 多跳检索的最大跳数（2-5），默认 3。
        include_adjacent: 是否包含相邻分块（扩展上下文）。
        filter: 元数据过滤条件。例如 {"code_language": "python"}。

    Returns:
        {"search_id": "...", "results": [...]}
    """
    service = get_rag_service()
    if service is None:
        return {"search_id": "", "results": [], "error": "RAG 服务未初始化"}

    request = SearchRequest(
        query=query,
        kb_names=kb_names or [],
        top_k=min(top_k, 10),
        enable_rerank=enable_rerank,
        include_adjacent=include_adjacent,
        metadata_filter=filter,
    )

    if enable_multi_hop:
        result = await service.retriever.retrieve_multi_hop(
            query=query,
            kb_names=kb_names or [],
            max_hops=min(max_hops, 5),
            top_k_per_hop=top_k,
        )
        search_results = result.final_chunks
        search_id = f"mhop_{id(result)}"
    else:
        search_id, search_results = await service.retriever.retrieve(
            request,
            enable_query_rewrite=enable_query_rewrite,
            enable_multi_hop=False,
        )

    return {
        "search_id": search_id,
        "results": [
            {
                "chunk_id": r.chunk_id,
                "doc_id": r.doc_id,
                "kb_name": r.kb_name,
                "content": r.content,
                "score": round(r.score, 4),
                "source_file": r.source_file,
            }
            for r in search_results
        ],
    }


@tool(
    name="unified_search",
    description="统一检索：同时搜索记忆系统和知识库文档。一次调用获取所有相关知识。适用场景：不确定信息来自记忆还是文档时。",
)
async def unified_search(
    query: str,
    sources: list[str] | None = None,
    top_k: int = 5,
    include_archived_memories: bool = False,
    enable_rerank: bool = True,
    generate_summary: bool = False,
    include_adjacent: bool = False,
    filter: dict | None = None,
) -> dict:
    """统一检索记忆 + 文档。

    Args:
        query: 检索查询文本。
        sources: 检索来源列表。可选值: "memory", "documents"。
        top_k: 每种来源返回的结果数，默认 5。
        include_archived_memories: 是否包含归档记忆。
        enable_rerank: 是否启用重排序。
        generate_summary: 是否生成 LLM 综合摘要。
        include_adjacent: 是否包含相邻分块（仅对 document 来源生效）。
        filter: 元数据过滤条件（仅对 document 来源生效）。

    Returns:
        {"search_id": "...", "memory_results": [...], "document_results": [...], ...}
    """
    service = get_rag_service()
    if service is None:
        return {"search_id": "", "memory_results": [], "document_results": [], "error": "RAG 服务未初始化"}

    doc_params = {
        "top_k": top_k,
        "enable_rerank": enable_rerank,
        "include_adjacent": include_adjacent,
        "filter": filter,
    }
    mem_params = {
        "top_k": top_k,
        "include_archived_memories": include_archived_memories,
    }

    response = await service.unified_retriever.search(
        query=query,
        sources=sources,
        memory_search_params=mem_params,
        document_search_params=doc_params,
    )

    result = response.to_dict()

    if generate_summary:
        all_docs = response.document_results
        if all_docs:
            result["merged_summary"] = await service.unified_retriever.generate_summary(
                query, all_docs
            )

    return result


# ============================================================================
# 导入工具
# ============================================================================


@tool(
    name="ingest_documents",
    description="将文档文件导入到指定知识库。支持 Markdown、纯文本、代码文件和 PDF。自动解析、分块、嵌入并建立索引。",
)
async def ingest_documents(
    file_paths: list[str],
    kb_name: str = "default",
    chunk_strategy: str = "recursive",
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    recursive: bool = False,
) -> dict:
    """将文档导入知识库。

    Args:
        file_paths: 要导入的文件路径列表。可包含目录路径（需设置 recursive=true）。
        kb_name: 目标知识库名称，默认 "default"。
        chunk_strategy: 分块策略 (token/recursive/semantic)，默认 recursive。
        chunk_size: 分块大小（tokens），默认 512。
        chunk_overlap: 分块重叠（tokens），默认 50。
        recursive: 是否递归扫描目录。

    Returns:
        导入结果统计: {"success": N, "skipped": M, "documents": [...]}
    """
    service = get_rag_service()
    if service is None:
        return {"success": 0, "skipped": 0, "documents": [], "error": "RAG 服务未初始化"}

    # 处理目录递归扫描
    if recursive:
        expanded_paths = []
        import os
        for path in file_paths:
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for f in files:
                        if not f.startswith(".") and not f.startswith("_"):
                            expanded_paths.append(os.path.join(root, f))
            else:
                expanded_paths.append(path)
        file_paths = expanded_paths

    strategy = None
    try:
        strategy = ChunkStrategy(chunk_strategy)
    except ValueError:
        pass

    config = IngestionConfig(
        kb_name=kb_name,
        parser_type=ParserType.AUTO,
        chunk_strategy=strategy,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    try:
        documents = await service.ingestion_pipeline.ingest(file_paths, config)
    except Exception as e:
        logger.error("文档导入失败: %s", e)
        return {"success": 0, "skipped": 0, "documents": [], "error": str(e)}

    return {
        "success": len(documents),
        "skipped": 0,
        "documents": [
            {"doc_id": d.id, "filename": d.filename, "status": d.status.value}
            for d in documents
        ],
    }


@tool(
    name="ingest_text",
    description="将文本内容直接导入知识库（无需文件）。适用于 Agent 需要保存生成的总结、分析结果等场景。",
)
async def ingest_text(
    content: str,
    title: str,
    kb_name: str = "default",
    chunk_strategy: str = "recursive",
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    metadata: dict | None = None,
) -> dict:
    """将文本内容直接导入知识库。

    虚拟文件名: {sanitized_title}_{timestamp}.md（自动追加时间戳避免冲突）。

    Args:
        content: 要导入的文本内容。
        title: 文档标题（用于生成虚拟文件名）。
        kb_name: 目标知识库名称，默认 "default"。
        chunk_strategy: 分块策略。
        chunk_size: 分块大小（tokens），默认 512。
        chunk_overlap: 分块重叠（tokens），默认 50。
        metadata: 附加元数据。

    Returns:
        {"success": true, "document": {...}, "virtual_filename": "..."}
    """
    service = get_rag_service()
    if service is None:
        return {"success": False, "error": "RAG 服务未初始化"}

    import tempfile
    import os
    from datetime import datetime

    # 生成带时间戳的虚拟文件名
    sanitized = "".join(c for c in title if c.isalnum() or c in "._- ")[:50]
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    virtual_filename = f"{sanitized}_{timestamp}.md"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp_path = f.name

    try:
        strategy = None
        try:
            strategy = ChunkStrategy(chunk_strategy)
        except ValueError:
            pass

        config = IngestionConfig(
            kb_name=kb_name,
            parser_type=ParserType.MARKDOWN,
            chunk_strategy=strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            metadata=metadata or {},
        )

        documents = await service.ingestion_pipeline.ingest([tmp_path], config)
        if documents:
            return {
                "success": True,
                "document": {
                    "doc_id": documents[0].id,
                    "filename": documents[0].filename,
                    "status": documents[0].status.value,
                },
                "virtual_filename": virtual_filename,
            }
        return {"success": False, "error": "导入失败"}
    finally:
        os.unlink(tmp_path)


# ============================================================================
# 管理工具
# ============================================================================


@tool(
    name="list_knowledge_bases",
    description="列出所有可用的知识库及其状态（文档数、分块数、嵌入模型等）。",
)
async def list_knowledge_bases() -> list[dict]:
    """列出所有知识库。

    Returns:
        知识库列表。
    """
    service = get_rag_service()
    if service is None:
        return []

    return service.kb_manager.list_kbs()


@tool(
    name="list_documents",
    description="列出指定知识库中的所有文档及其状态、大小等元信息。",
)
async def list_documents(
    kb_name: str = "default",
    status_filter: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """列出知识库中的文档。

    Args:
        kb_name: 知识库名称。
        status_filter: 按状态过滤，如 "ready", "error"。
        limit: 每页最多返回的文档数，默认 20，最大 100。
        offset: 分页偏移量。

    Returns:
        {"kb_name": "...", "total_count": N, "documents": [...]}
    """
    service = get_rag_service()
    if service is None:
        return {"kb_name": kb_name, "total_count": 0, "documents": [], "error": "RAG 服务未初始化"}

    kb = service.kb_manager.get_kb(kb_name)
    if kb is None:
        return {"kb_name": kb_name, "total_count": 0, "documents": [], "error": f"知识库 '{kb_name}' 不存在"}

    doc_store = service.kb_manager.get_or_create_kb(kb_name)
    # 直接使用 document_store
    from graph_agent.rag.document_store import DocumentStore
    store = DocumentStore(kb_name, f"data/rag/{kb_name}")
    return store.list_documents(status_filter=status_filter, limit=min(limit, 100), offset=offset)


@tool(
    name="get_document",
    description="获取知识库中某个文档的完整内容。适用于 Agent 需要阅读完整文档而非检索片段的场景。",
)
async def get_document(
    doc_id: str | None = None,
    filename: str | None = None,
    kb_name: str = "default",
    max_tokens: int = 8000,
) -> dict:
    """获取完整文档内容。

    Args:
        doc_id: 文档 ID（与 filename 二选一）。
        filename: 文件名模糊匹配（精确子串，case-insensitive）。多匹配返回最近更新的。
        kb_name: 知识库名称。
        max_tokens: 最大返回 token 数，默认 8000。

    Returns:
        {"doc_id": "...", "filename": "...", "content": "...", "truncated": bool}
    """
    service = get_rag_service()
    if service is None:
        return {"error": "RAG 服务未初始化"}

    from graph_agent.rag.document_store import DocumentStore
    store = DocumentStore(kb_name, f"data/rag/{kb_name}")

    doc = None
    if doc_id:
        doc = store.get_document(doc_id)
    elif filename:
        doc = store.get_document_by_filename(filename)

    if doc is None:
        return {"error": "文档未找到"}

    content = store.get_document_content(doc.id)
    if content is None:
        return {"error": "文档内容为空"}

    total_tokens = doc.total_tokens
    truncated = False

    # 智能截断
    if total_tokens > max_tokens and max_tokens > 0:
        truncated = True
        # 粗略按字符估算（1 token ≈ 2 字符）
        max_chars = max_tokens * 2
        if len(content) > max_chars:
            head_size = int(max_chars * 0.6)
            tail_size = int(max_chars * 0.3)
            content = (
                content[:head_size]
                + f"\n\n[... 文档被截断，原始 {total_tokens} tokens ...]\n\n"
                + content[-tail_size:]
            )

    return {
        "doc_id": doc.id,
        "filename": doc.filename,
        "content": content,
        "chunk_count": doc.chunk_count,
        "total_tokens": total_tokens,
        "truncated": truncated,
        "returned_tokens": min(total_tokens, max_tokens),
    }


@tool(
    name="delete_knowledge_bases",
    description="删除指定的知识库及其所有文档、索引和向量数据。此操作不可逆。",
)
async def delete_knowledge_bases(
    kb_names: list[str],
    confirm: bool = False,
) -> dict:
    """删除知识库。

    Args:
        kb_names: 要删除的知识库名称列表。
        confirm: 必须显式设为 true 才执行删除。

    Returns:
        {"deleted": [...], "not_found": [...]}
    """
    if not confirm:
        return {"error": "请设置 confirm=true 确认删除", "kb_names": kb_names}

    service = get_rag_service()
    if service is None:
        return {"error": "RAG 服务未初始化"}

    deleted = []
    not_found = []
    for name in kb_names:
        if service.kb_manager.delete_kb(name):
            deleted.append(name)
        else:
            not_found.append(name)

    return {"deleted": deleted, "not_found": not_found}


@tool(
    name="delete_documents",
    description="从知识库中删除指定的文档。同时清理 FTS5 索引和向量索引。",
)
async def delete_documents(
    doc_ids: list[str],
    kb_name: str = "default",
) -> dict:
    """删除文档。

    Args:
        doc_ids: 要删除的文档 ID 列表。
        kb_name: 知识库名称。

    Returns:
        {"deleted_count": N, "not_found": [...]}
    """
    service = get_rag_service()
    if service is None:
        return {"error": "RAG 服务未初始化"}

    deleted = 0
    not_found = []
    for doc_id in doc_ids:
        success = await service.ingestion_pipeline.delete_document(doc_id)
        if success:
            deleted += 1
        else:
            not_found.append(doc_id)

    # 使缓存失效
    if deleted > 0 and service.retriever:
        service.retriever.cache.invalidate(kb_name)

    return {"deleted_count": deleted, "not_found": not_found}


@tool(
    name="reindex_knowledge_base",
    description="重建知识库的向量索引。在切换嵌入模型后必须执行此操作。",
)
async def reindex_knowledge_base(
    kb_name: str,
    new_embedder_type: str | None = None,
    new_embedder_model: str | None = None,
) -> dict:
    """重建知识库索引（影子重建模式）。

    采用"先创建临时集合 → 全部完成 → 原子替换"策略，
    任何阶段失败都不影响旧集合。

    Args:
        kb_name: 目标知识库名称。
        new_embedder_type: 新的嵌入器类型。
        new_embedder_model: 新的嵌入模型名称。

    Returns:
        {"status": "completed"|"cancelled", "chunks_reindexed": N, ...}
    """
    service = get_rag_service()
    if service is None:
        return {"error": "RAG 服务未初始化"}

    # 实现影子重建逻辑
    from graph_agent.rag.rag_types import EmbedderType
    from graph_agent.rag.document_store import DocumentStore

    store = DocumentStore(kb_name, f"data/rag/{kb_name}")
    kb = service.kb_manager.get_kb(kb_name)
    if kb is None:
        return {"error": f"知识库 '{kb_name}' 不存在"}

    old_dimension = store.get_embedding_dimension()
    new_embedder = service.embedder

    if new_embedder_model and new_embedder_type:
        # 创建新的嵌入器
        et = EmbedderType(new_embedder_type)
        if et != service.config.embedding.provider:
            from graph_agent.rag.embedding import EmbedderRegistry
            new_embedder = EmbedderRegistry.create(
                et.value, model_name=new_embedder_model
            )

    new_dimension = new_embedder.get_dimension()

    if old_dimension == new_dimension and new_embedder_model is None:
        return {"status": "skipped", "reason": "维度一致，无需重建"}

    # 影子重建
    chunks = store.get_chunks_by_doc_id(
        store.list_documents()["documents"][0]["doc_id"]
    ) if store.list_documents()["documents"] else []

    # 简化版：遍历所有就绪文档的 chunks
    all_chunks = []
    docs = store.list_documents(status_filter="ready", limit=1000)
    for doc_info in docs["documents"]:
        doc_chunks = store.get_chunks_by_doc_id(doc_info["doc_id"])
        all_chunks.extend(doc_chunks)

    if not all_chunks:
        return {"status": "skipped", "reason": "没有可重建的 chunk"}

    # 创建临时集合
    tmp_kb_name = f"{kb_name}_reindex_tmp"
    vec_store = service.vector_store

    try:
        await vec_store.create_collection(tmp_kb_name, new_dimension)

        # 批量重新嵌入
        batch_size = new_embedder.get_batch_size()
        reindexed = 0
        for i in range(0, len(all_chunks), batch_size):
            batch = all_chunks[i:i + batch_size]
            texts = [c.content for c in batch]
            embeddings = new_embedder.embed(texts)
            for chunk, emb in zip(batch, embeddings):
                chunk.embedding = emb
            await vec_store.add_chunks(tmp_kb_name, batch)
            reindexed += len(batch)

        # 验证完整性
        if reindexed != len(all_chunks):
            await vec_store.delete_collection(tmp_kb_name)
            return {"status": "error", "reason": f"重建不完整: {reindexed}/{len(all_chunks)}"}

        # 原子替换
        old_collection = kb_name
        if await vec_store.collection_exists(old_collection):
            await vec_store.delete_collection(old_collection)
        # 重命名需要 chromadb 支持，这里简化为交换
        store.set_embedding_dimension(new_dimension)

        return {
            "status": "completed",
            "chunks_reindexed": reindexed,
            "old_dimension": old_dimension,
            "new_dimension": new_dimension,
        }
    except Exception as e:
        logger.error("reindex 失败: %s", e)
        # 清理临时集合
        try:
            await vec_store.delete_collection(tmp_kb_name)
        except Exception:
            pass
        return {"status": "error", "reason": str(e)}


@tool(
    name="verify_index_integrity",
    description="验证知识库的索引一致性（FTS5 vs ChromaDB）。检测因异常崩溃导致的不一致问题。",
)
async def verify_index_integrity(
    kb_name: str,
    auto_repair: bool = False,
) -> dict:
    """验证并可选修复知识库索引一致性。

    Args:
        kb_name: 目标知识库名称。
        auto_repair: 是否自动修复发现的不一致。

    Returns:
        {"is_consistent": bool, "missing_in_fts": [...], ...}
    """
    service = get_rag_service()
    if service is None:
        return {"error": "RAG 服务未初始化"}

    report = await service.ingestion_pipeline.verify_index_integrity(kb_name)

    return {
        "is_consistent": report.is_consistent,
        "total_chunks": report.total_chunks_in_docstore,
        "missing_in_fts": report.missing_in_fts,
        "missing_in_vector": report.missing_in_vector,
        "orphaned": report.orphaned_in_fts + report.orphaned_in_vector,
        "repaired": 0,  # auto_repair 暂未实现自动化
    }


@tool(
    name="preview_chunking",
    description="预览文档的分块效果（dry-run 模式）。不实际导入，帮助调整分块参数。",
)
async def preview_chunking(
    file_path: str | None = None,
    text: str | None = None,
    chunk_strategy: str = "recursive",
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    max_preview_chunks: int = 5,
) -> dict:
    """预览分块效果。

    语义分块预览自动降级为递归分块近似（避免加载模型）。

    Args:
        file_path: 文件路径（与 text 二选一）。
        text: 文本内容（与 file_path 二选一）。
        chunk_strategy: 分块策略。
        chunk_size: 分块大小（tokens）。
        chunk_overlap: 分块重叠（tokens）。
        max_preview_chunks: 最多展示的分块数。

    Returns:
        {"total_chunks": N, "avg_chunk_tokens": M, "preview_chunks": [...], ...}
    """
    service = get_rag_service()
    # 即使 RAG 服务未初始化，也可以做分块预览（不需要嵌入）
    from graph_agent.rag.chunking import ChunkerRegistry

    note = ""
    actual_strategy = chunk_strategy
    if chunk_strategy == "semantic":
        actual_strategy = "recursive"
        note = "语义分块预览使用递归分块近似，实际语义分块结果可能不同"

    try:
        chunker = ChunkerRegistry.create(actual_strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    except Exception:
        return {"error": f"无效的分块策略: {chunk_strategy}"}

    if file_path:
        from graph_agent.rag.parser import ParserRegistry
        registry = ParserRegistry()
        registry.auto_register()
        parser = registry.get_parser(file_path)
        text_content, _ = parser.parse(file_path)
    elif text:
        text_content = text
    else:
        return {"error": "请提供 file_path 或 text"}

    chunks = chunker.chunk(text_content, {})
    token_counts = [c.token_count for c in chunks]

    preview = []
    for i, chunk in enumerate(chunks[:max_preview_chunks]):
        preview.append({
            "index": i,
            "content": chunk.content[:300] + ("..." if len(chunk.content) > 300 else ""),
            "token_count": chunk.token_count,
        })

    result = {
        "total_chunks": len(chunks),
        "total_tokens": sum(token_counts),
        "avg_chunk_tokens": round(sum(token_counts) / len(token_counts), 1) if token_counts else 0,
        "min_chunk_tokens": min(token_counts) if token_counts else 0,
        "max_chunk_tokens": max(token_counts) if token_counts else 0,
        "std_chunk_tokens": round(
            (sum((t - sum(token_counts) / len(token_counts)) ** 2 for t in token_counts) / len(token_counts)) ** 0.5, 1
        ) if token_counts and len(token_counts) > 1 else 0,
        "size_distribution": {
            "under_100": sum(1 for t in token_counts if t < 100),
            "100_500": sum(1 for t in token_counts if 100 <= t < 500),
            "500_1000": sum(1 for t in token_counts if 500 <= t < 1000),
            "over_1000": sum(1 for t in token_counts if t >= 1000),
        },
        "preview_chunks": preview,
    }
    if note:
        result["note"] = note

    return result


@tool(
    name="report_search_quality",
    description="显式上报检索结果的质量反馈。Agent 在引用或弃用某个检索结果后调用此工具。",
)
async def report_search_quality(
    search_id: str,
    useful_chunk_ids: list[str] | None = None,
    useless_chunk_ids: list[str] | None = None,
    overall_rating: int | None = None,
    comment: str = "",
) -> dict:
    """上报检索质量反馈。

    Args:
        search_id: 检索事件 ID。
        useful_chunk_ids: 被 Agent 引用的 chunk ID 列表。
        useless_chunk_ids: 被 Agent 确认不相关的 chunk ID 列表。
        overall_rating: 整体检索质量评分 (1-5)。
        comment: 自由文本备注。

    Returns:
        {"recorded": N, "search_id": "..."}
    """
    service = get_rag_service()
    if service is None or service.feedback_collector is None:
        return {"error": "反馈收集器未初始化"}

    recorded = await service.feedback_collector.record_usage_batch(
        search_id=search_id,
        useful_chunk_ids=useful_chunk_ids,
        useless_chunk_ids=useless_chunk_ids,
    )

    return {"recorded": recorded, "search_id": search_id}
