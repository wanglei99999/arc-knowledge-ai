"""ES search filter 拼接单元测试——纯函数，不连 ES。"""

from app.domain.metadata_filter import FilterOperator, MetadataFilter
from app.infrastructure.elasticsearch.client import _build_search_filters


def test_filters_without_metadata() -> None:
    assert _build_search_filters("t1", "s1", None) == [
        {"term": {"tenant_id": "t1"}},
        {"term": {"space_id": "s1"}},
    ]


def test_filters_empty_metadata_list() -> None:
    assert _build_search_filters("t1", "s1", []) == [
        {"term": {"tenant_id": "t1"}},
        {"term": {"space_id": "s1"}},
    ]


def test_filters_with_metadata() -> None:
    filters = [MetadataFilter(key="project", operator=FilterOperator.EQ, value="A")]
    assert _build_search_filters("t1", "s1", filters) == [
        {"term": {"tenant_id": "t1"}},
        {"term": {"space_id": "s1"}},
        {"term": {"metadata.project": "A"}},
    ]


def test_filters_with_range_metadata() -> None:
    filters = [MetadataFilter(key="year", operator=FilterOperator.GTE, value=2023)]
    assert _build_search_filters("t1", "s1", filters) == [
        {"term": {"tenant_id": "t1"}},
        {"term": {"space_id": "s1"}},
        {"range": {"metadata.year": {"gte": 2023}}},
    ]
