from __future__ import annotations

import dataclasses
import time
import uuid
from dataclasses import replace
from typing import Any

from pydantic import BaseModel

from app.domain.retrieval import RetrievalQuery, SearchContext, SearchHit
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage
from app.services.tenant_config_service import TenantConfigService

_FAKE_QUOTA = QuotaSnapshot(
    max_documents=10000,
    max_storage_bytes=10 * 1024 ** 3,
    max_api_calls_per_day=100000,
    used_documents=0,
    used_storage_bytes=0,
    used_api_calls_today=0,
)

_ANSWER_SYSTEM_PROMPT = """\
你是一个知识库问答助手。请根据以下检索到的文档片段回答用户问题。
回答要准确、简洁，如果文档中没有相关信息，请如实说明，不要编造内容。

【参考文档】
{context}
"""


class DebugSearchRequest(BaseModel):
    query: str
    top_k: int = 10
    score_threshold: float = 0.5
    query_rewrite_enabled: bool = True
    rerank_enabled: bool = True
    vector_top_k: int = 20
    keyword_top_k: int = 20
    rrf_k: int = 60
    include_answer: bool = False


def _rrf_fuse(
    vector_hits: list[SearchHit],
    keyword_hits: list[SearchHit],
    top_k: int,
    rrf_k: int,
) -> list[SearchHit]:
    scores: dict[str, float] = {}
    hit_map: dict[str, SearchHit] = {}
    for hits in (vector_hits, keyword_hits):
        for rank, hit in enumerate(
            sorted(hits, key=lambda h: h.score, reverse=True), start=1
        ):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            hit_map.setdefault(hit.chunk_id, hit)
    sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)[:top_k]
    return [
        SearchHit(
            chunk_id=cid,
            document_id=hit_map[cid].document_id,
            chunk_index=hit_map[cid].chunk_index,
            score=scores[cid],
            source="rrf",
            rank=r,
        )
        for r, cid in enumerate(sorted_ids, 1)
    ]


def _unique_ids(*hit_lists: list[SearchHit]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for hits in hit_lists:
        for h in hits:
            if h.chunk_id not in seen:
                seen.add(h.chunk_id)
                result.append(h.chunk_id)
    return result


def _to_debug_hits(
    hits: list[SearchHit],
    content_map: dict[str, dict],
    source: str,
) -> list[dict[str, Any]]:
    out = []
    for h in hits:
        meta = content_map.get(h.chunk_id, {})
        out.append({
            "chunk_id":    h.chunk_id,
            "document_id": h.document_id,
            "doc_name":    meta.get("original_name", h.document_id),
            "chunk_index": h.chunk_index,
            "content":     meta.get("content", ""),
            "score":       h.score,
            "source":      source,
        })
    return out


class DebugRetrievalService:
    def __init__(self) -> None:
        self._chunk_repo = ChunkRepository()
        self._tenant_config_svc = TenantConfigService()

    def _make_ctx(self, tenant_id: str, config: TenantConfig) -> ProcessingContext:
        return ProcessingContext.create(
            tenant_id=tenant_id,
            document_id="",
            quota=_FAKE_QUOTA,
            config=config,
            trace_id=str(uuid.uuid4()),
        )

    async def debug_search(self, req: DebugSearchRequest, tenant_id: str) -> dict:
        base_config = await self._tenant_config_svc.get_or_default(tenant_id)
        config = dataclasses.replace(
            base_config,
            query_rewrite_enabled=req.query_rewrite_enabled,
            rerank_enabled=req.rerank_enabled,
        )
        ctx = self._make_ctx(tenant_id, config)

        base_query = RetrievalQuery(
            query_text=req.query,
            tenant_id=tenant_id,
            top_k=req.top_k,
            score_threshold=req.score_threshold,
        )
        search_ctx = SearchContext(query=base_query)
        timings: dict[str, float] = {}

        # Stage 1: QueryRewrite
        t = time.monotonic()
        search_ctx = await registry.get_stage("query_rewrite").execute(ctx, search_ctx)
        timings["query_rewrite"] = (time.monotonic() - t) * 1000

        # Stage 2: VectorSearch（使用 vector_top_k 作为候选数）
        t = time.monotonic()
        sc_v = replace(search_ctx, query=replace(search_ctx.query, top_k=req.vector_top_k))
        sc_v = await registry.get_stage("vector_search").execute(ctx, sc_v)
        search_ctx = replace(search_ctx, vector_hits=sc_v.vector_hits)
        timings["vector_search"] = (time.monotonic() - t) * 1000

        # Stage 3: KeywordSearch（使用 keyword_top_k 作为候选数）
        t = time.monotonic()
        sc_k = replace(search_ctx, query=replace(search_ctx.query, top_k=req.keyword_top_k))
        sc_k = await registry.get_stage("keyword_search").execute(ctx, sc_k)
        search_ctx = replace(search_ctx, keyword_hits=sc_k.keyword_hits)
        timings["keyword_search"] = (time.monotonic() - t) * 1000

        # Stage 4: RRF Fusion（内联，支持 rrf_k 参数覆盖）
        t = time.monotonic()
        rrf_hits = _rrf_fuse(
            search_ctx.vector_hits, search_ctx.keyword_hits, req.top_k, req.rrf_k
        )
        search_ctx = replace(search_ctx, ranked_hits=rrf_hits)
        timings["rrf_fusion"] = (time.monotonic() - t) * 1000

        # Stage 5: Rerank
        t = time.monotonic()
        rerank_ctx = replace(search_ctx, query=replace(search_ctx.query, top_k=req.top_k))
        final_hits: list[SearchHit] = await registry.get_stage("rerank").execute(ctx, rerank_ctx)
        timings["rerank"] = (time.monotonic() - t) * 1000

        # 批量拉取 chunk 原文（vector + keyword + final 的并集）
        t = time.monotonic()
        all_ids = _unique_ids(search_ctx.vector_hits, search_ctx.keyword_hits, final_hits)
        chunks = await self._chunk_repo.get_chunks_by_ids(all_ids, tenant_id)
        content_map: dict[str, dict] = {c["chunk_id"]: c for c in chunks}
        timings["chunk_fetch"] = (time.monotonic() - t) * 1000

        answer: str | None = None
        if req.include_answer:
            answer = await self._generate_answer(
                final_hits, content_map, req.query, ctx
            )

        return {
            "query_text":       req.query,
            "rewritten_queries": search_ctx.query.expanded_queries,
            "intent_is_valid":  search_ctx.query.intent_is_valid,
            "vector_hits":      _to_debug_hits(search_ctx.vector_hits, content_map, "vector"),
            "keyword_hits":     _to_debug_hits(search_ctx.keyword_hits, content_map, "keyword"),
            "rrf_hits":         _to_debug_hits(search_ctx.ranked_hits, content_map, "rrf"),
            "final_hits":       _to_debug_hits(final_hits, content_map, "rerank"),
            "timings_ms":       timings,
            "params":           req.model_dump(),
            "answer":           answer,
        }

    async def _generate_answer(
        self,
        hits: list[SearchHit],
        content_map: dict[str, dict],
        query_text: str,
        ctx: ProcessingContext,
    ) -> str:
        if not hits:
            return "未找到相关文档，无法生成答案。"

        context_parts = []
        for i, h in enumerate(hits, 1):
            content = content_map.get(h.chunk_id, {}).get("content", "")
            if content:
                context_parts.append(f"[{i}] {content}")

        context_str = "\n\n".join(context_parts) if context_parts else "（无可用文档内容）"
        system_prompt = _ANSWER_SYSTEM_PROMPT.format(context=context_str)

        from app.providers.llm.model_hub import model_hub
        provider = model_hub.get_provider(ctx)
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=query_text),
        ]
        try:
            return await provider.generate(ctx, messages)
        except Exception:
            return "答案生成失败，请检查 LLM 配置。"
