from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import require_tenant
from app.services.retrieval_service import RetrievalService, SearchRequest

router = APIRouter(prefix="/search", tags=["retrieval"])
_service = RetrievalService()


@router.get("")
async def search(
    q: str = Query(..., description="检索查询词"),
    space_id: str = Query(..., description="知识空间 ID"),
    top_k: int = Query(default=10, ge=1, le=50),
    score_threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    tenant_id: str = Depends(require_tenant),
) -> dict:
    """
    混合检索接口（向量 + BM25 + RRF）。

    返回相关 chunk 列表及其原文内容。
    """
    req = SearchRequest(
        query=q,
        tenant_id=tenant_id,
        top_k=top_k,
        score_threshold=score_threshold,
    )
    resp = await _service.search(req)
    return {
        "query":  resp.query,
        "total":  resp.total,
        "hits":   resp.hits,
        "chunks": resp.chunks,
    }
