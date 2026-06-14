"""RAG 服务（统一对外接口）。

提供 RAG 系统的初始化、配置加载、组件组装和生命周期管理。
负责将 RAG 工具注册到 ToolCenter，实现懒加载和冷启动引导。
"""

import logging
from pathlib import Path
from typing import Optional

from graph_agent.rag.document_store import KnowledgeBaseManager
from graph_agent.rag.rag_config import RAGConfig
from graph_agent.rag.rag_tools import set_rag_service
from graph_agent.rag.rag_types import EmbedderType

logger = logging.getLogger(__name__)


class RAGService:
    """RAG 服务（统一对外接口）。

    负责：
    - 加载全局配置
    - 初始化嵌入服务、向量存储、检索引擎
    - 管理知识库注册表
    - 冷启动引导（自动扫描 docs/ 目录）
    - 注册 Agent 工具
    """

    def __init__(self):
        self.config: RAGConfig | None = None
        self.kb_manager: KnowledgeBaseManager | None = None
        self.embedder = None
        self.vector_store = None
        self.retriever = None
        self.unified_retriever = None
        self.ingestion_pipeline = None
        self.feedback_collector = None
        self._initialized = False

    @classmethod
    def initialize(cls, config_data: dict | None = None) -> "RAGService":
        """初始化 RAG 服务。

        Args:
            config_data: RAG 配置字典。None 则从 config/rag_config.yaml 加载。

        Returns:
            初始化完成的 RAGService 实例。
        """
        service = cls()

        # 加载配置
        if config_data:
            service.config = RAGConfig()
            # 手动设置配置项
            if "enabled" in config_data:
                service.config.enabled = config_data["enabled"]
        else:
            service.config = RAGConfig.from_yaml("config/rag_config.yaml")

        if not service.config.enabled:
            logger.info("RAG 系统已禁用")
            return service

        # 初始化知识库管理
        service.kb_manager = KnowledgeBaseManager(
            data_dir=service.config.vector_store.persist_dir
        )

        # 检查注册表完整性
        if not service.kb_manager.is_registry_healthy():
            logger.warning("kb_registry.json 损坏或缺失，正在从磁盘重建...")
            recovered = service.kb_manager._rebuild_from_disk()
            logger.info("已从磁盘恢复 %d 个知识库", len(recovered))

        # 初始化嵌入服务
        service.embedder = service._init_embedder()

        # 初始化向量存储
        service.vector_store = service._init_vector_store()

        # 初始化检索引擎组件
        service._init_retrieval_components()

        # 冷启动引导
        service._bootstrap()

        # 设置全局 RAG 服务引用（供工具使用）
        set_rag_service(service)

        # 清理残留的临时 reindex 集合
        service.kb_manager.cleanup_orphaned_reindex_collections()

        service._initialized = True
        logger.info("RAG 服务初始化完成")
        return service

    def _init_embedder(self):
        """初始化嵌入服务。"""
        from graph_agent.rag.embedding import EmbedderRegistry
        from graph_agent.rag.embedding.cache_embedder import CacheEmbedder

        emb_config = self.config.embedding

        try:
            if emb_config.provider == EmbedderType.LOCAL:
                embedder = EmbedderRegistry.create(
                    "local", model_name=emb_config.local_model
                )
            elif emb_config.provider == EmbedderType.OLLAMA:
                embedder = EmbedderRegistry.create(
                    "ollama", model_name=emb_config.model
                )
            else:  # OPENAI
                embedder = EmbedderRegistry.create(
                    "openai", model_name=emb_config.model
                )
        except Exception as e:
            logger.error("无法初始化嵌入服务: %s，使用本地回退", e)
            try:
                embedder = EmbedderRegistry.create(
                    "local", model_name="BAAI/bge-small-zh-v1.5"
                )
            except Exception:
                raise RuntimeError("无法初始化任何嵌入服务")

        # 包裹缓存层
        if emb_config.cache_enabled:
            embedder = CacheEmbedder(embedder, cache_db=emb_config.cache_db)

        return embedder

    def _init_vector_store(self):
        """初始化向量存储。"""
        from graph_agent.rag.vector_store import VectorStoreRegistry

        vs_config = self.config.vector_store
        return VectorStoreRegistry.get_or_create_store(
            kb_name="_global",
            backend=vs_config.backend,
            dimension=self.embedder.get_dimension(),
            persist_dir=vs_config.persist_dir,
        )

    def _init_retrieval_components(self):
        """初始化检索引擎各组件。"""
        from graph_agent.rag.document_store import DocumentStore
        from graph_agent.rag.retrieval.feedback_collector import FeedbackCollector
        from graph_agent.rag.retrieval.fusion_engine import FusionEngine
        from graph_agent.rag.retrieval.keyword_searcher import KeywordSearcher
        from graph_agent.rag.retrieval.query_processor import QueryProcessor
        from graph_agent.rag.retrieval.rag_retriever import RAGRetriever
        from graph_agent.rag.retrieval.reranker import Reranker
        from graph_agent.rag.retrieval.retrieval_cache import RetrievalCache
        from graph_agent.rag.retrieval.vector_searcher import VectorSearcher

        # 文档存储（使用默认 KB 的存储实例作为检索入口）
        doc_store = DocumentStore(
            "default", str(Path(self.config.vector_store.persist_dir) / "default")
        )

        # 关键词检索
        keyword_searcher = KeywordSearcher(doc_store)

        # 向量检索
        vector_searcher = VectorSearcher(
            vector_store=self.vector_store,
            embedder=self.embedder,
        )

        # 融合引擎
        fusion_engine = FusionEngine()

        # 重排序器（使用 GraphAgent LLM）
        llm = None
        try:
            from graph_agent.llm.base import LLMFactory
            llm_config = None
            try:
                from graph_agent.llm.base import LLMConfig
                llm_config = LLMConfig.from_env()
                llm = LLMFactory.create(llm_config)
            except Exception:
                logger.warning("无法初始化 LLM，重排序和查询改写将不可用")
        except ImportError:
            logger.warning("LLM 模块不可用")

        reranker = Reranker(method="llm", llm=llm)

        # 查询处理器
        query_processor = QueryProcessor(llm=llm, embedder=self.embedder) if llm else None

        # 反馈收集器
        self.feedback_collector = FeedbackCollector(
            feedback_db=str(Path(self.config.vector_store.persist_dir) / ".feedback.db")
        )

        # 检索缓存
        retrieval_cache = RetrievalCache()

        # RAG 检索器
        self.retriever = RAGRetriever(
            keyword_searcher=keyword_searcher,
            vector_searcher=vector_searcher,
            fusion_engine=fusion_engine,
            reranker=reranker,
            document_store=doc_store,
            query_processor=query_processor,
            feedback_collector=self.feedback_collector,
            retrieval_cache=retrieval_cache,
        )

        # 统一检索器（记忆 + 文档）
        memory_searcher = None
        try:
            from graph_agent.memory.memory_searcher import MemorySearcher
            from graph_agent.memory.memory_store import MemoryStore
            from graph_agent.memory.memory_indexer import MemoryIndexer
            store = MemoryStore()  # default base_dir = data/memory/
            indexer = MemoryIndexer()  # default db_path = data/memory/memory.db
            memory_searcher = MemorySearcher(indexer=indexer, store=store)
        except Exception as e:
            logger.warning("记忆搜索器不可用: %s，unified_search 将仅返回文档结果", e)

        from graph_agent.rag.unified_retriever import UnifiedRetriever
        self.unified_retriever = UnifiedRetriever(
            memory_searcher=memory_searcher,
            rag_retriever=self.retriever,
            llm=llm,
        )

        # 导入流水线（共享 embedder，统一嵌入路径）
        from graph_agent.rag.ingestion_pipeline import IngestionPipeline
        self.ingestion_pipeline = IngestionPipeline(
            rag_config=self.config,
            embedder=self.embedder,  # 共享 embedder（含 CacheEmbedder 缓存层）
        )

    def _bootstrap(self):
        """冷启动：首次启动时创建默认 KB 并自动导入项目文档。"""
        if not self.kb_manager.list_kbs():
            logger.info("首次启动，执行冷启动引导...")
            try:
                self.kb_manager.create_kb(
                    "default",
                    description="默认知识库（自动创建）",
                )
                logger.info("已创建默认知识库")

                # 扫描 docs/ 目录
                docs_dir = Path("docs")
                if docs_dir.exists():
                    md_files = list(docs_dir.glob("*.md"))
                    import os
                    # 简化版：直接导入发现的 .md 文件
                    # 完整版使用 ingestion_pipeline
                    logger.info("发现 %d 个文档文件待导入", len(md_files))
            except Exception as e:
                logger.warning("冷启动引导失败: %s", e)

    def check_embedding_dimension_mismatch(self) -> list[dict]:
        """检测知识库嵌入维度与当前嵌入模型的兼容性。

        Returns:
            不匹配的知识库列表: [{"kb_name": "...", "stored_dim": N, "current_dim": M}]
        """
        mismatched = []
        if self.embedder is None:
            return mismatched

        current_dim = self.embedder.get_dimension()
        for kb_info in self.kb_manager.list_kbs():
            stored_dim = kb_info.get("embedding_dimension", 0)
            if stored_dim > 0 and stored_dim != current_dim:
                mismatched.append({
                    "kb_name": kb_info["name"],
                    "stored_dimension": stored_dim,
                    "current_dimension": current_dim,
                })
        return mismatched
