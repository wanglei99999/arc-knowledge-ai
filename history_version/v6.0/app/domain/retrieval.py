from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievalQuery:
    """检索请求，贯穿整条 Retrieval Pipeline"""
    query_text: str
    tenant_id: str
    top_k: int = 10
    score_threshold: float = 0.5
    # QueryRewriteStage 填充；为空时各 SearchStage 直接用 query_text
    expanded_queries: list[str] = field(default_factory=list)


@dataclass
class SearchHit:
    """单条检索命中记录"""
    chunk_id: str
    document_id: str
    chunk_index: int
    score: float
    source: str = "unknown"   # "vector" | "keyword"
    rank: int = 0             # RRFFusionStage 填充


@dataclass
class SearchContext:
    """
    Retrieval Pipeline 的数据载体。
    各 Stage 读写此对象，最终由 RRFFusionStage 合并输出 list[SearchHit]。
    """
    query: RetrievalQuery
    vector_hits: list[SearchHit] = field(default_factory=list)
    keyword_hits: list[SearchHit] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """检索最终结果，交给 RAGOrchestrator 做生成"""
    query_text: str
    hits: list[SearchHit]
    # 从 PostgreSQL 查回的 chunk 文本，key 为 chunk_id
    chunks: list[dict]        # [{chunk_id, content, document_id, chunk_index, ...}]

    @property
    def context_text(self) -> str:
        """拼接 chunk 文本，用于 LLM prompt"""
        ordered = sorted(self.chunks, key=lambda c: c.get("chunk_index", 0))
        return "\n\n".join(c["content"] for c in ordered)
