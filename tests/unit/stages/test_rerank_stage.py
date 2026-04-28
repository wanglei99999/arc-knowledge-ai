"""RerankStage 单元测试"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.retrieval import RetrievalQuery, SearchContext, SearchHit
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.stages.retrieval.rerank_stage import RerankStage
from app.providers.base import HealthStatus, RerankProvider


# ── Fake RerankProvider ───────────────────────────────────────────────────────

class FakeRerankProvider(RerankProvider):
    """按文档长度倒序打分（长文档得分高），方便断言顺序变化。"""
    provider_id = "fake_rerank"

    async def health_check(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def rerank(self, ctx, query: str, documents: list[str], top_n: int):
        scored = [(i, float(len(doc))) for i, doc in enumerate(documents)]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_n]


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ctx(rerank_enabled: bool = True, rerank_provider: str = "fake_rerank") -> ProcessingContext:
    quota = QuotaSnapshot(
        max_documents=100, max_storage_bytes=1024, max_api_calls_per_day=1000,
        used_documents=0, used_storage_bytes=0, used_api_calls_today=0,
    )
    config = TenantConfig(
        tenant_id="test-tenant",
        rerank_enabled=rerank_enabled,
        rerank_provider=rerank_provider,
    )
    return ProcessingContext.create(
        tenant_id="test-tenant",
        document_id="",
        quota=quota,
        config=config,
    )


def _hit(chunk_id: str, rank: int = 1) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id, document_id="doc-1",
        chunk_index=rank - 1, score=1.0 / rank, rank=rank,
    )


def _search_ctx(hits: list[SearchHit], query_text: str = "test query") -> SearchContext:
    return SearchContext(
        query=RetrievalQuery(query_text=query_text, tenant_id="test-tenant"),
        ranked_hits=hits,
    )


# ── rerank_enabled=False ──────────────────────────────────────────────────────

async def test_rerank_disabled_returns_rrf_order() -> None:
    hits = [_hit("c1", 1), _hit("c2", 2), _hit("c3", 3)]
    stage = RerankStage(provider=FakeRerankProvider())
    ctx = _make_ctx(rerank_enabled=False)
    result = await stage._execute(ctx, _search_ctx(hits))
    assert [h.chunk_id for h in result] == ["c1", "c2", "c3"]


async def test_empty_hits_returns_empty() -> None:
    stage = RerankStage(provider=FakeRerankProvider())
    ctx = _make_ctx()
    result = await stage._execute(ctx, _search_ctx([]))
    assert result == []


# ── 正常精排 ───────────────────────────────────────────────────────────────────

async def test_rerank_reorders_hits() -> None:
    hits = [_hit("short", 1), _hit("much-longer-chunk-id", 2)]
    stage = RerankStage(provider=FakeRerankProvider())

    # FakeRerankProvider 按文档文本长度打分
    # chunk "short" → content "short content"（13字符）
    # chunk "much-longer-chunk-id" → content "much longer content here"（24字符）
    # 精排后 much-longer-chunk-id 应排到第一
    chunks = [
        {"chunk_id": "short", "content": "short content"},
        {"chunk_id": "much-longer-chunk-id", "content": "much longer content here"},
    ]

    _CHUNK_REPO = "app.infrastructure.postgres.repositories.chunk_repo.ChunkRepository"
    with patch(_CHUNK_REPO) as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_chunks_by_ids.return_value = chunks
        MockRepo.return_value = mock_repo

        ctx = _make_ctx()
        result = await stage._execute(ctx, _search_ctx(hits))

    assert result[0].chunk_id == "much-longer-chunk-id"
    assert result[1].chunk_id == "short"


async def test_rerank_assigns_new_scores_and_ranks() -> None:
    hits = [_hit("c1", 1), _hit("c2", 2)]
    chunks = [
        {"chunk_id": "c1", "content": "short"},
        {"chunk_id": "c2", "content": "much longer text here"},
    ]

    _CHUNK_REPO = "app.infrastructure.postgres.repositories.chunk_repo.ChunkRepository"
    with patch(_CHUNK_REPO) as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_chunks_by_ids.return_value = chunks
        MockRepo.return_value = mock_repo

        stage = RerankStage(provider=FakeRerankProvider())
        ctx = _make_ctx()
        result = await stage._execute(ctx, _search_ctx(hits))

    assert result[0].rank == 1
    assert result[1].rank == 2
    # 新分数来自 reranker，不是原始 RRF 分数
    assert result[0].score > result[1].score


# ── 降级路径 ───────────────────────────────────────────────────────────────────

async def test_provider_not_found_returns_rrf_order() -> None:
    hits = [_hit("c1", 1), _hit("c2", 2)]
    stage = RerankStage()  # 不注入 provider

    with patch("app.pipeline.stages.retrieval.rerank_stage.registry") as mock_registry:
        mock_registry.get_provider.side_effect = KeyError("not found")
        ctx = _make_ctx()
        result = await stage._execute(ctx, _search_ctx(hits))

    assert [h.chunk_id for h in result] == ["c1", "c2"]


async def test_chunk_fetch_failure_falls_back_to_rrf_order() -> None:
    hits = [_hit("c1", 1), _hit("c2", 2)]

    _CHUNK_REPO = "app.infrastructure.postgres.repositories.chunk_repo.ChunkRepository"
    with patch(_CHUNK_REPO) as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_chunks_by_ids.side_effect = RuntimeError("DB error")
        MockRepo.return_value = mock_repo

        stage = RerankStage(provider=FakeRerankProvider())
        ctx = _make_ctx()
        result = await stage._execute(ctx, _search_ctx(hits))

    assert [h.chunk_id for h in result] == ["c1", "c2"]
