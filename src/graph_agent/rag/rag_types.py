"""RAG 系统数据类型定义。

包含文档、分块、知识库、检索请求/结果、导入配置、
检索反馈、索引一致性报告等核心数据模型。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ============================================================================
# 枚举类型
# ============================================================================


class ChunkStrategy(str, Enum):
    """分块策略枚举。"""
    TOKEN = "token"            # Token-based 固定大小分块
    RECURSIVE = "recursive"    # 递归分块（按自然分隔符）
    SEMANTIC = "semantic"      # 语义分块（基于嵌入相似度边界）


class ParserType(str, Enum):
    """文档解析器类型。"""
    AUTO = "auto"              # 自动检测
    MARKDOWN = "markdown"      # Markdown 文件
    TEXT = "text"              # 纯文本文件
    CODE = "code"              # 代码文件
    PDF = "pdf"                # PDF 文件（可选）


class EmbedderType(str, Enum):
    """嵌入器类型。"""
    OPENAI = "openai"          # OpenAI 兼容 API
    LOCAL = "local"            # 本地 sentence-transformers
    OLLAMA = "ollama"          # Ollama 嵌入模型
    ANTHROPIC = "anthropic"    # Anthropic 嵌入（Voyage 等）


class VectorStoreType(str, Enum):
    """向量库类型。"""
    CHROMADB = "chromadb"      # ChromaDB（默认，轻量级）
    MILVUS_LITE = "milvus_lite"  # Milvus Lite（大规模场景）


class FusionMethod(str, Enum):
    """融合方法。"""
    WEIGHTED_SUM = "weighted_sum"  # 加权求和
    RRF = "rrf"                     # 倒数排名融合 (Reciprocal Rank Fusion)


class DocumentStatus(str, Enum):
    """文档状态枚举。

    状态转换规则：
    IMPORTING   -> READY       (导入成功)
    IMPORTING   -> ERROR       (导入失败或被取消)
    READY       -> REIMPORTING (update_document 触发)
    REIMPORTING -> READY       (重新导入成功)
    REIMPORTING -> ERROR       (重新导入失败)
    READY/ERROR -> DELETED     (delete_document 触发)
    ERROR       -> IMPORTING   (resume_ingestion 触发续跑)
    """
    IMPORTING = "importing"        # 导入中（阶段 A 或 B 进行中）
    READY = "ready"                # 就绪（可被检索）
    ERROR = "error"                # 错误（导入失败，不可检索）
    REIMPORTING = "reimporting"    # 重新导入中
    DELETED = "deleted"            # 已删除（软删除，保留记录后清理）


# ============================================================================
# 核心数据类
# ============================================================================


@dataclass
class Document:
    """文档模型。"""
    id: str                              # 文档唯一 ID
    kb_name: str                         # 所属知识库名称
    filename: str                        # 原始文件名
    file_type: str                       # 文件类型（md, txt, py, pdf 等）
    parser_type: ParserType              # 使用的解析器
    content_hash: str                    # 原始内容 SHA256 哈希（去重用）
    status: DocumentStatus = DocumentStatus.IMPORTING  # 文档状态
    status_message: str = ""             # 状态附加信息（如错误原因）
    chunk_count: int = 0                 # 分块数量
    total_tokens: int = 0                # 总 token 数
    metadata: dict = field(default_factory=dict)  # 自定义元数据
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Chunk:
    """文档分块模型。"""
    id: str                              # 分块唯一 ID
    doc_id: str                          # 所属文档 ID
    kb_name: str                         # 所属知识库名称
    content: str                         # 分块文本内容
    chunk_index: int                     # 在文档中的分块序号
    token_count: int                     # Token 数量
    content_hash: str = ""               # chunk 内容的 SHA256 哈希（嵌入去重用）
    embedding: Optional[list[float]] = None  # 向量嵌入
    metadata: dict = field(default_factory=dict)  # 元数据
    # metadata 包含:
    #   - source_file: 来源文件名
    #   - page_number: 页码（PDF）
    #   - heading_path: 标题路径（Markdown）
    #   - code_language: 代码语言（代码文件）
    #   - prev_chunk_id / next_chunk_id: 相邻分块 ID（上下文扩展用）


@dataclass
class KnowledgeBase:
    """知识库模型。"""
    name: str                            # 知识库名称（唯一标识）
    description: str = ""                # 描述
    embedder_type: EmbedderType = EmbedderType.OPENAI
    embedder_config: dict = field(default_factory=dict)  # 嵌入模型配置
    embedding_model: str = ""            # 实际使用的嵌入模型名称
    embedding_dimension: int = 0         # 嵌入向量维度，用于检测模型切换
    chunk_strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    chunk_size: int = 512                # 分块大小（tokens）
    chunk_overlap: int = 50              # 分块重叠（tokens）
    vector_store_type: VectorStoreType = VectorStoreType.CHROMADB
    fusion_method: FusionMethod = FusionMethod.WEIGHTED_SUM
    keyword_weight: float = 0.3          # 关键词检索权重（0-1）
    vector_weight: float = 0.7           # 向量检索权重（0-1）
    similarity_threshold: float = 0.5    # 相似度阈值
    document_count: int = 0              # 文档数量
    chunk_count: int = 0                 # 分块数量
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class SearchRequest:
    """检索请求。"""
    query: str                           # 查询文本
    kb_names: list[str] = field(default_factory=list)  # 目标知识库列表
    top_k: int = 5                       # 返回结果数
    fusion_method: FusionMethod = FusionMethod.WEIGHTED_SUM
    keyword_weight: float = 0.3
    vector_weight: float = 0.7
    similarity_threshold: float = 0.5
    enable_rerank: bool = True           # 是否启用重排序
    rerank_top_k: int = 20               # 重排序前保留的候选数
    include_metadata: bool = True        # 是否返回元数据
    include_adjacent: bool = False       # 是否返回相邻分块（上下文扩展）
    metadata_filter: dict | None = None  # 元数据过滤条件


@dataclass
class SearchResult:
    """检索结果。"""
    chunk_id: str                        # 分块 ID
    doc_id: str                          # 文档 ID
    kb_name: str                         # 知识库名称
    content: str                         # 分块内容
    score: float                         # 综合得分
    keyword_score: float = 0.0           # 关键词匹配分
    vector_score: float = 0.0            # 向量相似度分
    rerank_score: Optional[float] = None  # 重排序得分
    source_file: str = ""                # 来源文件名
    metadata: dict = field(default_factory=dict)


@dataclass
class IngestionConfig:
    """文档导入配置。"""
    kb_name: str                         # 目标知识库
    parser_type: ParserType = ParserType.AUTO
    chunk_strategy: Optional[ChunkStrategy] = None  # None 则使用知识库默认策略
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    metadata: dict = field(default_factory=dict)
    skip_duplicates: bool = True         # 是否跳过重复文档（按 content_hash）


@dataclass
class RetrievalFeedback:
    """检索反馈记录。

    用于追踪检索结果被 Agent 实际使用的情况，形成隐式反馈闭环。
    """
    id: str                              # 反馈记录唯一 ID
    query: str                           # 原始查询文本
    chunk_id: str                        # 被评估的分块 ID
    kb_name: str                         # 所属知识库
    search_score: float                  # 检索阶段得分
    rerank_score: Optional[float] = None  # 重排序阶段得分
    was_used: bool = False               # Agent 是否实际引用/使用了该结果
    usage_quality: Optional[int] = None  # 使用质量评分 (1-5, 可选人工标注)
    agent_id: str = ""                   # 发起检索的 Agent ID
    task_id: str = ""                    # 关联的任务 ID
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ProcessedQuery:
    """预处理后的查询。"""
    original: str                        # 原始查询
    rewritten: str = ""                  # 改写后的查询
    sub_queries: list[str] = field(default_factory=list)  # 分解后的子查询
    hyde_vector: Optional[list[float]] = None  # HyDE 向量
    strategies_used: list[str] = field(default_factory=list)


@dataclass
class MultiHopResult:
    """多跳检索结果。"""
    final_chunks: list  # list[SearchResult]  合并去重后的最终结果
    hops: list  # list[HopResult]            每跳的详细信息
    total_hops: int                      # 实际执行跳数
    sufficiency_score: float             # 最终充分性评分


@dataclass
class HopResult:
    """单跳检索结果。"""
    hop_index: int                       # 跳数序号（从 1 开始）
    query: str                           # 本轮使用的查询
    results: list  # list[SearchResult]  本轮检索结果
    new_information: list[str]           # 本轮新增的信息（chunk_id 列表）


@dataclass
class IntegrityReport:
    """索引一致性验证报告。"""
    kb_name: str
    is_consistent: bool                  # 是否完全一致
    total_chunks_in_docstore: int        # DocumentStore 中的 chunk 总数
    total_chunks_in_fts: int             # FTS5 索引中的 chunk 数
    total_chunks_in_vector: int          # ChromaDB 中的 chunk 数
    missing_in_fts: list[str] = field(default_factory=list)     # FTS5 缺失
    missing_in_vector: list[str] = field(default_factory=list)  # ChromaDB 缺失
    orphaned_in_fts: list[str] = field(default_factory=list)    # FTS5 孤立
    orphaned_in_vector: list[str] = field(default_factory=list) # ChromaDB 孤立


@dataclass
class FeedbackStats:
    """检索反馈统计。"""
    total_searches: int = 0              # 总检索次数
    total_chunks_returned: int = 0       # 返回的 chunk 总数
    chunks_used: int = 0                 # 被引用的 chunk 数
    usage_rate: float = 0.0              # chunk 使用率
    avg_used_score: float = 0.0          # 被使用 chunk 的平均检索得分
    avg_unused_score: float = 0.0        # 未被使用 chunk 的平均检索得分
    top_queries: list[str] = field(default_factory=list)  # 高频查询
    low_quality_chunks: list[str] = field(default_factory=list)  # 低质量 chunk
