from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.metadata_filter import MetadataFilter
from app.domain.retrieval import RetrievalResult
from app.workflows.rag_orchestrator import RAGOrchestrator

_orchestrator = RAGOrchestrator()


@dataclass
class SearchRequest:
    query: str
    tenant_id: str
    space_id: str = "default"
    top_k: int = 10
    score_threshold: float = 0.5
    metadata_filters: list[MetadataFilter] = field(default_factory=list)


@dataclass
class SearchResponse:
    query: str
    hits: list[dict]  # [{chunk_id, document_id, chunk_index, score, source}]
    chunks: list[dict]  # [{chunk_id, content, document_id, chunk_index, ...}]
    total: int


class RetrievalService:
    """封装检索逻辑，对 API 层屏蔽 Pipeline 细节。"""

    async def search(self, req: SearchRequest) -> SearchResponse:
        result: RetrievalResult = await _orchestrator.retrieve(
            query_text=req.query,
            tenant_id=req.tenant_id,
            space_id=req.space_id,
            top_k=req.top_k,
            score_threshold=req.score_threshold,
            metadata_filters=req.metadata_filters,
        )
        hits = [
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "chunk_index": h.chunk_index,
                "score": h.score,
                "source": h.source,
                "rank": h.rank,
            }
            for h in result.hits
        ]
        return SearchResponse(
            query=result.query_text,
            hits=hits,
            chunks=result.chunks,
            total=len(hits),
        )
