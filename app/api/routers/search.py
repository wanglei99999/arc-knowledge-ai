from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import require_tenant
from app.services.debug_retrieval_service import DebugRetrievalService, DebugSearchRequest
from app.services.retrieval_service import RetrievalService, SearchRequest

router = APIRouter(prefix="/search", tags=["retrieval"])
_service = RetrievalService()
_debug_service = DebugRetrievalService()


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


@router.post("/debug", summary="检索调试（逐阶段结果 + 耗时）")
async def debug_search(
    req: DebugSearchRequest,
    tenant_id: str = Depends(require_tenant),
) -> dict:
    """
    调试端点：逐阶段执行检索 Pipeline，返回每阶段命中结果和耗时。

    支持参数覆盖（query_rewrite_enabled / rerank_enabled / vector_top_k 等），
    方便调优时对比不同配置的效果。
    """
    return await _debug_service.debug_search(req, tenant_id)
