"""ES filter 子句编译单元测试——纯函数，无外部依赖。"""

from app.domain.metadata_filter import FilterOperator, MetadataFilter
from app.infrastructure.elasticsearch.filter_builder import build_es_clauses


def _f(key: str, op: str, value) -> MetadataFilter:
    return MetadataFilter(key=key, operator=FilterOperator(op), value=value)


def test_empty_returns_empty_list() -> None:
    assert build_es_clauses([]) == []


def test_eq_term() -> None:
    assert build_es_clauses([_f("project", "eq", "A")]) == [{"term": {"metadata.project": "A"}}]


def test_ne_must_not_term() -> None:
    assert build_es_clauses([_f("k", "ne", "x")]) == [
        {"bool": {"must_not": [{"term": {"metadata.k": "x"}}]}}
    ]


def test_gte_range() -> None:
    assert build_es_clauses([_f("year", "gte", 2023)]) == [
        {"range": {"metadata.year": {"gte": 2023}}}
    ]


def test_lt_range() -> None:
    assert build_es_clauses([_f("year", "lt", 2000)]) == [
        {"range": {"metadata.year": {"lt": 2000}}}
    ]


def test_in_terms() -> None:
    assert build_es_clauses([_f("status", "in", ["a", "b"])]) == [
        {"terms": {"metadata.status": ["a", "b"]}}
    ]


def test_nin_must_not_terms() -> None:
    assert build_es_clauses([_f("status", "nin", ["a"])]) == [
        {"bool": {"must_not": [{"terms": {"metadata.status": ["a"]}}]}}
    ]


def test_multiple_clauses() -> None:
    clauses = build_es_clauses([_f("p", "eq", "A"), _f("year", "gte", 2023)])
    assert clauses == [
        {"term": {"metadata.p": "A"}},
        {"range": {"metadata.year": {"gte": 2023}}},
    ]
