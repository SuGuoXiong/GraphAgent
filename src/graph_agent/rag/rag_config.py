"""RAG 系统配置管理。

支持从 YAML 文件或环境变量加载配置，
全局配置和知识库级配置分层管理。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from graph_agent.rag.rag_types import (
    ChunkStrategy,
    EmbedderType,
    FusionMethod,
    VectorStoreType,
)


@dataclass
class EmbeddingConfig:
    """嵌入模型配置。"""
    provider: EmbedderType = EmbedderType.OPENAI
    model: str = "text-embedding-3-small"
    local_model: str = "BAAI/bge-small-zh-v1.5"
    cache_enabled: bool = True
    cache_db: str = "data/rag/.embed_cache.db"
    batch_size: int = 32
    rate_limit: int = 10  # 每秒最大请求数


@dataclass
class VectorStoreConfig:
    """向量存储配置。"""
    backend: VectorStoreType = VectorStoreType.CHROMADB
    persist_dir: str = "data/rag/"


@dataclass
class RetrievalConfig:
    """检索参数配置。"""
    default_top_k: int = 5
    max_top_k: int = 20
    fusion_method: FusionMethod = FusionMethod.WEIGHTED_SUM
    keyword_weight: float = 0.3
    vector_weight: float = 0.7
    similarity_threshold: float = 0.5
    rerank_candidate_count: int = 20


@dataclass
class RerankConfig:
    """重排序配置。"""
    enabled: bool = True
    method: str = "llm"  # llm / cross_encoder
    cross_encoder_model: str = "BAAI/bge-reranker-v2-m3"
    llm_prompt: str = "rag_rerank"


@dataclass
class IngestionDefaultsConfig:
    """文档导入默认配置。"""
    default_chunk_strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    default_chunk_size: int = 512
    default_chunk_overlap: int = 50
    skip_duplicates: bool = True
    supported_extensions: list[str] = field(default_factory=lambda: [
        ".md", ".markdown", ".txt", ".py", ".js", ".ts",
        ".java", ".go", ".yaml", ".yml", ".json", ".pdf",
    ])


@dataclass
class RAGConfig:
    """全局 RAG 配置。

    从 YAML 文件加载，路径默认为 config/rag_config.yaml。
    """
    enabled: bool = True
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = field(default_factory=VectorStoreConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    rerank: RerankConfig = field(default_factory=RerankConfig)
    ingestion: IngestionDefaultsConfig = field(default_factory=IngestionDefaultsConfig)

    @classmethod
    def from_yaml(cls, path: str | Path = "config/rag_config.yaml") -> "RAGConfig":
        """从 YAML 配置文件加载 RAG 配置。"""
        config_path = Path(path)
        if not config_path.exists():
            # 配置不存在时返回默认配置
            return cls()

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        rag_data = data.get("rag", {})
        if not rag_data:
            return cls()

        config = cls()

        # 嵌入配置
        emb = rag_data.get("embedding", {})
        if emb:
            config.embedding = EmbeddingConfig(
                provider=EmbedderType(emb.get("provider", "openai")),
                model=emb.get("model", "text-embedding-3-small"),
                local_model=emb.get("local_model", "BAAI/bge-small-zh-v1.5"),
                cache_enabled=emb.get("cache_enabled", True),
                cache_db=emb.get("cache_db", "data/rag/.embed_cache.db"),
                batch_size=emb.get("batch_size", 32),
                rate_limit=emb.get("rate_limit", 10),
            )

        # 向量存储配置
        vs = rag_data.get("vector_store", {})
        if vs:
            config.vector_store = VectorStoreConfig(
                backend=VectorStoreType(vs.get("backend", "chromadb")),
                persist_dir=vs.get("persist_dir", "data/rag/"),
            )

        # 检索配置
        ret = rag_data.get("retrieval", {})
        if ret:
            config.retrieval = RetrievalConfig(
                default_top_k=ret.get("default_top_k", 5),
                max_top_k=ret.get("max_top_k", 20),
                fusion_method=FusionMethod(ret.get("fusion_method", "weighted_sum")),
                keyword_weight=ret.get("keyword_weight", 0.3),
                vector_weight=ret.get("vector_weight", 0.7),
                similarity_threshold=ret.get("similarity_threshold", 0.5),
                rerank_candidate_count=ret.get("rerank_candidate_count", 20),
            )

        # 重排序配置
        rerank = rag_data.get("rerank", {})
        if rerank:
            config.rerank = RerankConfig(
                enabled=rerank.get("enabled", True),
                method=rerank.get("method", "llm"),
                cross_encoder_model=rerank.get("cross_encoder_model", "BAAI/bge-reranker-v2-m3"),
                llm_prompt=rerank.get("llm_prompt", "rag_rerank"),
            )

        # 导入配置
        ingest = rag_data.get("ingestion", {})
        if ingest:
            config.ingestion = IngestionDefaultsConfig(
                default_chunk_strategy=ChunkStrategy(
                    ingest.get("default_chunk_strategy", "recursive")
                ),
                default_chunk_size=ingest.get("default_chunk_size", 512),
                default_chunk_overlap=ingest.get("default_chunk_overlap", 50),
                skip_duplicates=ingest.get("skip_duplicates", True),
                supported_extensions=ingest.get("supported_extensions", config.ingestion.supported_extensions),
            )

        config.enabled = rag_data.get("enabled", True)
        return config


@dataclass
class KnowledgeBaseConfig:
    """知识库级配置。

    每个知识库有独立的 kb_config.yaml，存储在 data/rag/{kb_name}/ 下。
    """
    schema_version: int = 1
    name: str = ""
    description: str = ""
    embedder_provider: EmbedderType = EmbedderType.OPENAI
    embedder_model: str = "text-embedding-3-small"
    chunk_strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    chunk_size: int = 512
    chunk_overlap: int = 50
    vector_store_backend: VectorStoreType = VectorStoreType.CHROMADB
    fusion_method: FusionMethod = FusionMethod.WEIGHTED_SUM
    keyword_weight: float = 0.3
    vector_weight: float = 0.7
    similarity_threshold: float = 0.5

    CURRENT_SCHEMA_VERSION = 1

    @classmethod
    def from_yaml(cls, path: str | Path) -> "KnowledgeBaseConfig":
        """从 YAML 文件加载知识库配置。"""
        config_path = Path(path)
        if not config_path.exists():
            return cls()

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = cls()
        config.schema_version = data.get("schema_version", 0)
        config.name = data.get("name", "")
        config.description = data.get("description", "")

        emb = data.get("embedder", {})
        if emb:
            config.embedder_provider = EmbedderType(emb.get("provider", "openai"))
            config.embedder_model = emb.get("model", "text-embedding-3-small")

        chunk = data.get("chunk", {})
        if chunk:
            config.chunk_strategy = ChunkStrategy(chunk.get("strategy", "recursive"))
            config.chunk_size = chunk.get("size", 512)
            config.chunk_overlap = chunk.get("overlap", 50)

        vs = data.get("vector_store", {})
        if vs:
            config.vector_store_backend = VectorStoreType(vs.get("backend", "chromadb"))

        ret = data.get("retrieval", {})
        if ret:
            config.fusion_method = FusionMethod(ret.get("fusion_method", "weighted_sum"))
            config.keyword_weight = ret.get("keyword_weight", 0.3)
            config.vector_weight = ret.get("vector_weight", 0.7)
            config.similarity_threshold = ret.get("similarity_threshold", 0.5)

        # 根据 schema_version 执行迁移
        config = cls._migrate(config)

        return config

    def to_yaml(self, path: str | Path) -> None:
        """将知识库配置写入 YAML 文件。"""
        data = {
            "schema_version": self.CURRENT_SCHEMA_VERSION,
            "name": self.name,
            "description": self.description,
            "embedder": {
                "provider": self.embedder_provider.value,
                "model": self.embedder_model,
            },
            "chunk": {
                "strategy": self.chunk_strategy.value,
                "size": self.chunk_size,
                "overlap": self.chunk_overlap,
            },
            "vector_store": {
                "backend": self.vector_store_backend.value,
            },
            "retrieval": {
                "fusion_method": self.fusion_method.value,
                "keyword_weight": self.keyword_weight,
                "vector_weight": self.vector_weight,
                "similarity_threshold": self.similarity_threshold,
            },
        }
        config_path = Path(path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)

    @classmethod
    def _migrate(cls, config: "KnowledgeBaseConfig") -> "KnowledgeBaseConfig":
        """根据 schema_version 执行配置迁移。"""
        while config.schema_version < cls.CURRENT_SCHEMA_VERSION:
            if config.schema_version == 0:
                # v0 -> v1: 初始版本，无需迁移
                pass
            config.schema_version += 1
        return config
