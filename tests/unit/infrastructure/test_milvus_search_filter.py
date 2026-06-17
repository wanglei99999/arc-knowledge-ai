"""Milvus search filter 拼接单元测试——纯函数，不连 Milvus。"""

from app.domain.metadata_filter import FilterOperator, MetadataFilter
from app.infrastructure.milvus.client import _build_search_filter


def test_filter_without_metadata() -> None:
    assert _build_search_filter("t1", "s1", None) == 'tenant_id == "t1" and space_id == "s1"'


def test_filter_empty_metadata_list() -> None:
    assert _build_search_filter("t1", "s1", []) == 'tenant_id == "t1" and space_id == "s1"'


def test_filter_with_metadata() -> None:
    filters = [MetadataFilter(key="project", operator=FilterOperator.EQ, value="A")]
    assert (
        _build_search_filter("t1", "s1", filters)
        == 'tenant_id == "t1" and space_id == "s1" and metadata["project"] == "A"'
    )


def test_filter_with_multiple_metadata() -> None:
    filters = [
        MetadataFilter(key="project", operator=FilterOperator.EQ, value="A"),
        MetadataFilter(key="year", operator=FilterOperator.GTE, value=2023),
    ]
    assert (
        _build_search_filter("t1", "s1", filters) == 'tenant_id == "t1" and space_id == "s1" '
        'and metadata["project"] == "A" and metadata["year"] >= 2023'
    )
