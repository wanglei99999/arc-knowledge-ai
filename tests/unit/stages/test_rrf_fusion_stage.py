"""RRFFusionStage 单元测试"""
import pytest

from app.domain.retrieval import RetrievalQuery, SearchContext, SearchHit
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.stages.retrieval.rrf_fusion_stage import RRFFusionStage


@pytest.fixture
def ctx() -> ProcessingContext:
    quota = QuotaSnapshot(
        max_documents=100, max_storage_bytes=1024, max_api_calls_per_day=1000,
        used_documents=0, used_storage_bytes=0, used_api_calls_today=0,
    )
    return ProcessingContext.create(
        tenant_id="test-tenant",
        document_id="",
        quota=quota,
        config=TenantConfig(tenant_id="test-tenant"),
    )


def _query(top_k: int = 5) -> RetrievalQuery:
    return RetrievalQuery(query_text="test", tenant_id="test-tenant", top_k=top_k)


def _hit(chunk_id: str, score: float, source: str = "vector") -> SearchHit:
    return SearchHit(chunk_id=chunk_id, document_id="doc-1", chunk_index=0, score=score, source=source)


@pytest.fixture
def stage() -> RRFFusionStage:
    return RRFFusionStage()


# ── 输出类型 ───────────────────────────────────────────────────────────────────

async def test_returns_search_context(ctx, stage) -> None:
    search_ctx = SearchContext(
        query=_query(),
        vector_hits=[_hit("c1", 0.9)],
        keyword_hits=[_hit("c2", 5.0, "keyword")],
    )
    result = await stage._execute(ctx, search_ctx)
    assert isinstance(result, SearchContext)


async def test_ranked_hits_populated(ctx, stage) -> None:
    search_ctx = SearchContext(
        query=_query(),
        vector_hits=[_hit("c1", 0.9), _hit("c2", 0.7)],
        keyword_hits=[_hit("c2", 5.0, "keyword"), _hit("c3", 3.0, "keyword")],
    )
    result = await stage._execute(ctx, search_ctx)
    assert len(result.ranked_hits) > 0


# ── RRF 融合逻辑 ───────────────────────────────────────────────────────────────

async def test_overlap_chunk_gets_higher_score(ctx, stage) -> None:
    # c1 同时出现在向量和关键词结果中，分数应高于只出现一次的 c2
    search_ctx = SearchContext(
        query=_query(top_k=10),
        vector_hits=[_hit("c1", 0.9), _hit("c2", 0.5)],
        keyword_hits=[_hit("c1", 4.0, "keyword"), _hit("c3", 2.0, "keyword")],
    )
    result = await stage._execute(ctx, search_ctx)
    scores = {h.chunk_id: h.score for h in result.ranked_hits}
    assert scores["c1"] > scores["c2"]
    assert scores["c1"] > scores["c3"]


async def test_top_k_respected(ctx, stage) -> None:
    vector_hits = [_hit(f"v{i}", float(10 - i)) for i in range(8)]
    keyword_hits = [_hit(f"k{i}", float(10 - i), "keyword") for i in range(8)]
    search_ctx = SearchContext(query=_query(top_k=5), vector_hits=vector_hits, keyword_hits=keyword_hits)
    result = await stage._execute(ctx, search_ctx)
    assert len(result.ranked_hits) == 5


async def test_ranks_assigned_sequentially(ctx, stage) -> None:
    search_ctx = SearchContext(
        query=_query(top_k=10),
        vector_hits=[_hit("c1", 0.9), _hit("c2", 0.7), _hit("c3", 0.5)],
        keyword_hits=[],
    )
    result = await stage._execute(ctx, search_ctx)
    ranks = [h.rank for h in result.ranked_hits]
    assert ranks == list(range(1, len(ranks) + 1))


async def test_empty_hits_returns_empty_ranked(ctx, stage) -> None:
    search_ctx = SearchContext(query=_query(), vector_hits=[], keyword_hits=[])
    result = await stage._execute(ctx, search_ctx)
    assert result.ranked_hits == []


async def test_only_vector_hits(ctx, stage) -> None:
    search_ctx = SearchContext(
        query=_query(top_k=10),
        vector_hits=[_hit("c1", 0.9), _hit("c2", 0.5)],
        keyword_hits=[],
    )
    result = await stage._execute(ctx, search_ctx)
    assert len(result.ranked_hits) == 2


async def test_query_preserved_in_output(ctx, stage) -> None:
    query = _query(top_k=3)
    search_ctx = SearchContext(query=query, vector_hits=[_hit("c1", 0.9)], keyword_hits=[])
    result = await stage._execute(ctx, search_ctx)
    assert result.query is query
