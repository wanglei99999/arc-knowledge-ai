"""第 4 阶段：检索 Stage 透传 metadata_filters 单元测试。

验证 VectorSearchStage / KeywordSearchStage 将 RetrievalQuery.metadata_filters
原样传给底层 search_vectors / bm25_search（照 space_id 透传模板）。
"""

from __future__ import annotations

from app.domain.metadata_filter import FilterOperator, MetadataFilter
from app.domain.retrieval import RetrievalQuery, SearchContext
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.stages.retrieval import keyword_search_stage, vector_search_stage
from app.pipeline.stages.retrieval.keyword_search_stage import KeywordSearchStage
from app.pipeline.stages.retrieval.vector_search_stage import VectorSearchStage
from app.providers.base import EmbeddingProvider, HealthStatus


class FakeEmbeddingProvider(EmbeddingProvider):
    provider_id = "fake_embedding"

    async def health_check(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def embed(self, ctx: ProcessingContext, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]

    def get_dimension(self) -> int:
        return 8

    def get_model_name(self) -> str:
        return "fake"


def _make_ctx() -> ProcessingContext:
    quota = QuotaSnapshot(
        max_documents=100,
        max_storage_bytes=1024,
        max_api_calls_per_day=1000,
        used_documents=0,
        used_storage_bytes=0,
        used_api_calls_today=0,
    )
    return ProcessingContext.create(
        tenant_id="t1",
        document_id="",
        quota=quota,
        config=TenantConfig(tenant_id="t1"),
    )


def _filters() -> list[MetadataFilter]:
    return [MetadataFilter(key="project", operator=FilterOperator.EQ, value="A")]


def test_retrieval_query_metadata_filters_default_empty() -> None:
    q = RetrievalQuery(query_text="x", tenant_id="t1")
    assert q.metadata_filters == []


async def test_vector_search_stage_passes_metadata_filters(monkeypatch) -> None:
    captured: dict = {}

    async def fake_search_vectors(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(vector_search_stage, "search_vectors", fake_search_vectors)

    stage = VectorSearchStage(provider=FakeEmbeddingProvider())
    query = RetrievalQuery(query_text="x", tenant_id="t1", metadata_filters=_filters())
    await stage._execute(_make_ctx(), SearchContext(query=query))

    assert captured["metadata_filters"] == _filters()


async def test_keyword_search_stage_passes_metadata_filters(monkeypatch) -> None:
    captured: dict = {}

    async def fake_bm25_search(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(keyword_search_stage, "bm25_search", fake_bm25_search)

    stage = KeywordSearchStage()
    query = RetrievalQuery(query_text="x", tenant_id="t1", metadata_filters=_filters())
    await stage._execute(_make_ctx(), SearchContext(query=query))

    assert captured["metadata_filters"] == _filters()
