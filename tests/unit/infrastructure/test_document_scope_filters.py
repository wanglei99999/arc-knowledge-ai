from __future__ import annotations

import pytest

from app.domain.metadata_filter import FilterOperator, MetadataFilter
from app.infrastructure.elasticsearch import client as es_client
from app.infrastructure.elasticsearch.client import _build_search_filters
from app.infrastructure.milvus import client as milvus_client
from app.infrastructure.milvus.client import _build_search_filter

DOC1 = "11111111-1111-4111-8111-111111111111"
DOC2 = "22222222-2222-4222-8222-222222222222"


def test_milvus_filter_includes_document_scope() -> None:
    expr = _build_search_filter("tenant-1", "space-1", document_ids=[DOC1, DOC2])

    assert expr == (
        f'tenant_id == "tenant-1" and space_id == "space-1" and document_id in ["{DOC1}", "{DOC2}"]'
    )


def test_es_filter_includes_document_scope() -> None:
    clauses = _build_search_filters("tenant-1", "space-1", document_ids=[DOC1, DOC2])

    assert clauses == [
        {"term": {"tenant_id": "tenant-1"}},
        {"term": {"space_id": "space-1"}},
        {"terms": {"document_id": [DOC1, DOC2]}},
    ]


@pytest.mark.parametrize("document_ids", [None, []])
def test_empty_document_scope_keeps_full_space_filters(document_ids) -> None:
    milvus = _build_search_filter("tenant-1", "space-1", document_ids=document_ids)
    elasticsearch = _build_search_filters("tenant-1", "space-1", document_ids=document_ids)

    assert milvus == 'tenant_id == "tenant-1" and space_id == "space-1"'
    assert elasticsearch == [
        {"term": {"tenant_id": "tenant-1"}},
        {"term": {"space_id": "space-1"}},
    ]


@pytest.mark.parametrize("builder", [_build_search_filter, _build_search_filters])
def test_document_scope_rejects_invalid_uuid(builder) -> None:
    with pytest.raises(ValueError):
        builder("tenant-1", "space-1", document_ids=['bad-id" or true'])


def test_document_scope_and_metadata_filter_coexist() -> None:
    metadata = [MetadataFilter(key="project", operator=FilterOperator.EQ, value="A")]

    milvus = _build_search_filter(
        "tenant-1",
        "space-1",
        metadata_filters=metadata,
        document_ids=[DOC1],
    )
    elasticsearch = _build_search_filters(
        "tenant-1",
        "space-1",
        metadata_filters=metadata,
        document_ids=[DOC1],
    )

    assert f'document_id in ["{DOC1}"]' in milvus
    assert 'metadata["project"] == "A"' in milvus
    assert {"terms": {"document_id": [DOC1]}} in elasticsearch
    assert {"term": {"metadata.project": "A"}} in elasticsearch


async def test_search_vectors_applies_document_scope_to_milvus_request(
    monkeypatch,
) -> None:
    captured = {}

    class _Client:
        def has_collection(self, name):
            return True

        def search(self, **kwargs):
            captured.update(kwargs)
            return [[]]

    monkeypatch.setattr(milvus_client, "_get_client", _Client)

    await milvus_client.search_vectors(
        query_vector=[0.1, 0.2],
        tenant_id="tenant-1",
        space_id="space-1",
        document_ids=[DOC1],
    )

    assert f'document_id in ["{DOC1}"]' in captured["filter"]


async def test_bm25_search_applies_document_scope_to_es_request(monkeypatch) -> None:
    captured = {}

    class _Client:
        def search(self, **kwargs):
            captured.update(kwargs)
            return {"hits": {"hits": []}}

    monkeypatch.setattr(es_client, "_get_client", _Client)

    await es_client.bm25_search(
        query_text="金额",
        tenant_id="tenant-1",
        space_id="space-1",
        document_ids=[DOC1],
    )

    filters = captured["body"]["query"]["bool"]["filter"]
    assert {"terms": {"document_id": [DOC1]}} in filters
