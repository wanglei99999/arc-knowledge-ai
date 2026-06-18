"""第 4 阶段：RetrievalService 透传 metadata_filters 单元测试。"""

from __future__ import annotations

from app.domain.metadata_filter import FilterOperator, MetadataFilter
from app.domain.retrieval import RetrievalResult
from app.services import retrieval_service
from app.services.retrieval_service import RetrievalService, SearchRequest


async def test_search_threads_metadata_filters(monkeypatch) -> None:
    captured: dict = {}

    async def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return RetrievalResult(query_text=kwargs["query_text"], hits=[], chunks=[])

    monkeypatch.setattr(retrieval_service._orchestrator, "retrieve", fake_retrieve)

    filters = [MetadataFilter(key="project", operator=FilterOperator.EQ, value="A")]
    req = SearchRequest(query="x", tenant_id="t1", metadata_filters=filters)
    await RetrievalService().search(req)

    assert captured["metadata_filters"] == filters


async def test_search_default_no_filters(monkeypatch) -> None:
    captured: dict = {}

    async def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return RetrievalResult(query_text=kwargs["query_text"], hits=[], chunks=[])

    monkeypatch.setattr(retrieval_service._orchestrator, "retrieve", fake_retrieve)

    await RetrievalService().search(SearchRequest(query="x", tenant_id="t1"))

    assert captured["metadata_filters"] == []
