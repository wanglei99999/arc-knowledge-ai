from __future__ import annotations

from app.domain.retrieval import RetrievalQuery, SearchContext
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.stages.retrieval import keyword_search_stage, vector_search_stage
from app.pipeline.stages.retrieval.keyword_search_stage import KeywordSearchStage
from app.pipeline.stages.retrieval.vector_search_stage import VectorSearchStage
from app.providers.base import EmbeddingProvider, HealthStatus

DOC1 = "11111111-1111-4111-8111-111111111111"
DOC2 = "22222222-2222-4222-8222-222222222222"


class _EmbeddingProvider(EmbeddingProvider):
    provider_id = "document-scope-test"

    async def health_check(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def embed(
        self, ctx: ProcessingContext, texts: list[str]
    ) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]

    def get_dimension(self) -> int:
        return 8

    def get_model_name(self) -> str:
        return "document-scope-test"


def _context() -> ProcessingContext:
    quota = QuotaSnapshot(
        max_documents=100,
        max_storage_bytes=1024,
        max_api_calls_per_day=1000,
        used_documents=0,
        used_storage_bytes=0,
        used_api_calls_today=0,
    )
    return ProcessingContext.create(
        tenant_id="tenant-1",
        document_id="",
        quota=quota,
        config=TenantConfig(tenant_id="tenant-1"),
    )


def test_retrieval_query_document_scope_defaults_empty() -> None:
    query = RetrievalQuery(query_text="金额", tenant_id="tenant-1")

    assert query.document_ids == []


async def test_vector_search_stage_passes_document_scope(monkeypatch) -> None:
    captured: dict = {}

    async def fake_search_vectors(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(vector_search_stage, "search_vectors", fake_search_vectors)
    query = RetrievalQuery(
        query_text="金额",
        tenant_id="tenant-1",
        document_ids=[DOC1, DOC2],
    )

    await VectorSearchStage(provider=_EmbeddingProvider())._execute(
        _context(), SearchContext(query=query)
    )

    assert captured["document_ids"] == [DOC1, DOC2]


async def test_keyword_search_stage_passes_document_scope(monkeypatch) -> None:
    captured: dict = {}

    async def fake_bm25_search(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(keyword_search_stage, "bm25_search", fake_bm25_search)
    query = RetrievalQuery(
        query_text="金额",
        tenant_id="tenant-1",
        document_ids=[DOC1, DOC2],
    )

    await KeywordSearchStage()._execute(_context(), SearchContext(query=query))

    assert captured["document_ids"] == [DOC1, DOC2]
