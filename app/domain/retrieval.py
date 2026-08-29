from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.metadata_filter import MetadataFilter


def normalize_document_ids(document_ids: Sequence[str] | None) -> list[str]:
    """校验并统一为小写连字符 UUID，供各检索后端安全构造过滤条件。"""
    if not document_ids:
        return []
    return [str(uuid.UUID(document_id)) for document_id in document_ids]


@dataclass
class RetrievalQuery:
    """检索请求，贯穿整条 Retrieval Pipeline"""

    query_text: str
    tenant_id: str
    space_id: str = "default"
    top_k: int = 10
    score_threshold: float = 0.5
    # QueryRewriteStage 填充；为空时各 SearchStage 直接用 query_text
    expanded_queries: list[str] = field(default_factory=list)
    # QueryRewriteStage 意图识别结果；False 时各 SearchStage 返回空 hits
    intent_is_valid: bool = True
    # 通用元数据过滤条件；各 SearchStage 透传给 Milvus/ES（为空时不过滤）
    metadata_filters: list[MetadataFilter] = field(default_factory=list)
    # 系统级文档范围；为空时检索整个 space，非空时只检索指定文档的 chunks
    document_ids: list[str] = field(default_factory=list)


@dataclass
class SearchHit:
    """单条检索命中记录"""

    chunk_id: str
    document_id: str
    chunk_index: int
    score: float
    source: str = "unknown"  # "vector" | "keyword"
    rank: int = 0  # RRFFusionStage 填充


@dataclass
class SearchContext:
    """
    Retrieval Pipeline 的数据载体，贯穿整条 Retrieval Pipeline。

    各 Stage 只读已有字段并返回新实例（不可变模式）：
    - QueryRewriteStage  → 填 expanded_queries / intent_is_valid
    - VectorSearchStage  → 填 vector_hits
    - KeywordSearchStage → 填 keyword_hits
    - RRFFusionStage     → 填 ranked_hits
    - RerankStage        → 读 ranked_hits，输出最终 list[SearchHit]
    """

    query: RetrievalQuery
    vector_hits: list[SearchHit] = field(default_factory=list)
    keyword_hits: list[SearchHit] = field(default_factory=list)
    # RRFFusionStage 填充；RerankStage 读取此字段进行精排
    ranked_hits: list[SearchHit] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """检索最终结果，交给 RAGOrchestrator 做生成"""

    query_text: str
    hits: list[SearchHit]
    # 从 PostgreSQL 查回的 chunk 文本，key 为 chunk_id
    chunks: list[dict]  # [{chunk_id, content, document_id, chunk_index, ...}]

    @property
    def context_text(self) -> str:
        """按检索相关性排序（最高分在前）拼接 chunk 文本，含文档来源标签"""
        chunk_map = {c["chunk_id"]: c for c in self.chunks}
        parts: list[str] = []
        for i, hit in enumerate(self.hits, 1):
            chunk = chunk_map.get(hit.chunk_id)
            if not chunk:
                continue
            doc_name = chunk.get("original_name") or hit.document_id
            parts.append(f"[{i}] 来源：{doc_name}\n{chunk['content']}")
        return "\n\n".join(parts)
