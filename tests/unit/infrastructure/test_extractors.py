from __future__ import annotations

from app.domain.retrieval import RetrievalQuery, SearchContext, SearchHit
from app.infrastructure.telemetry.extractors import EXTRACTORS, RetrievalExtractor


def _ctx_with_hits() -> SearchContext:
    q = RetrievalQuery(
        query_text="违约金上限是多少",
        tenant_id="t1",
        expanded_queries=["合同违约金上限", "违约金比例"],
    )
    hit = SearchHit(chunk_id="c1", document_id="d1", chunk_index=0, score=0.87, source="vector")
    return SearchContext(query=q, vector_hits=[hit], keyword_hits=[], ranked_hits=[hit])


def test_registry_contains_retrieval_extractor():
    assert any(isinstance(e, RetrievalExtractor) for e in EXTRACTORS)


def test_query_rewrite_attrs():
    ex = RetrievalExtractor()
    sc = _ctx_with_hits()
    assert ex.matches("query_rewrite", sc)
    attrs = ex.extract("query_rewrite", sc)
    assert attrs["openinference.span.kind"] == "CHAIN"
    assert attrs["input.value"] == "违约金上限是多少"
    assert attrs["output.value"] == "合同违约金上限 | 违约金比例"
    assert attrs["arc.retrieval.intent_is_valid"] is True


def test_vector_search_attrs():
    ex = RetrievalExtractor()
    attrs = ex.extract("vector_search", _ctx_with_hits())
    assert attrs["openinference.span.kind"] == "RETRIEVER"
    assert attrs["retrieval.documents.0.document.id"] == "c1"
    assert attrs["retrieval.documents.0.document.score"] == 0.87


def test_rerank_attrs_from_hit_list():
    ex = RetrievalExtractor()
    hits = [SearchHit(chunk_id="c9", document_id="d9", chunk_index=1, score=0.95)]
    assert ex.matches("rerank", hits)
    attrs = ex.extract("rerank", hits)
    assert attrs["openinference.span.kind"] == "RERANKER"
    assert attrs["reranker.output_documents.0.document.id"] == "c9"


def test_unknown_stage_not_matched():
    assert not RetrievalExtractor().matches("parser", object())


def test_capture_content_off_hides_text(monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "otel_capture_content", False)
    attrs = RetrievalExtractor().extract("query_rewrite", _ctx_with_hits())
    assert "input.value" not in attrs and "output.value" not in attrs
    assert attrs["arc.retrieval.intent_is_valid"] is True  # 结构照记
