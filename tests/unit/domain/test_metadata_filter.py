"""metadata_filter DSL 解析单元测试——纯函数，无外部依赖。"""

import pytest

from app.domain.metadata_filter import FilterOperator, parse_filters


def test_parse_valid_eq_filter() -> None:
    filters = parse_filters([{"key": "project", "operator": "eq", "value": "G2024"}])
    assert len(filters) == 1
    assert filters[0].key == "project"
    assert filters[0].operator is FilterOperator.EQ
    assert filters[0].value == "G2024"


def test_parse_empty_list_returns_empty() -> None:
    assert parse_filters([]) == []


def test_parse_rejects_injection_key() -> None:
    with pytest.raises(ValueError):
        parse_filters([{"key": 'x"; drop', "operator": "eq", "value": "a"}])


def test_parse_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        parse_filters([{"key": "", "operator": "eq", "value": "a"}])


def test_parse_rejects_unknown_operator() -> None:
    with pytest.raises(ValueError):
        parse_filters([{"key": "k", "operator": "like", "value": "a"}])


def test_parse_in_requires_list_value() -> None:
    with pytest.raises(ValueError):
        parse_filters([{"key": "k", "operator": "in", "value": "not_a_list"}])


def test_parse_in_rejects_empty_list() -> None:
    with pytest.raises(ValueError):
        parse_filters([{"key": "k", "operator": "in", "value": []}])


def test_parse_in_accepts_list() -> None:
    filters = parse_filters([{"key": "status", "operator": "in", "value": ["a", "b"]}])
    assert filters[0].operator is FilterOperator.IN
    assert filters[0].value == ["a", "b"]


def test_parse_accepts_numeric_value() -> None:
    filters = parse_filters([{"key": "year", "operator": "gte", "value": 2023}])
    assert filters[0].value == 2023


def test_parse_multiple_filters_preserve_order() -> None:
    filters = parse_filters(
        [
            {"key": "project", "operator": "eq", "value": "A"},
            {"key": "year", "operator": "gte", "value": 2023},
        ]
    )
    assert [f.key for f in filters] == ["project", "year"]
