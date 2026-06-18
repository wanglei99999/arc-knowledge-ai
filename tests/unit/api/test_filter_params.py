"""第 4 阶段：API 边界 metadata 过滤条件解析/校验单元测试。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.filter_params import parse_metadata_filters, parse_metadata_filters_json
from app.domain.metadata_filter import FilterOperator

# ── list[dict] 形式（POST body）─────────────────────────────────────────────


def test_none_returns_empty() -> None:
    assert parse_metadata_filters(None) == []


def test_empty_list_returns_empty() -> None:
    assert parse_metadata_filters([]) == []


def test_valid_filters_parsed() -> None:
    out = parse_metadata_filters([{"key": "project", "operator": "eq", "value": "A"}])
    assert len(out) == 1
    assert out[0].key == "project"
    assert out[0].operator == FilterOperator.EQ


def test_invalid_key_raises_400() -> None:
    with pytest.raises(HTTPException) as ei:
        parse_metadata_filters([{"key": 'x"; drop', "operator": "eq", "value": "A"}])
    assert ei.value.status_code == 400


def test_invalid_operator_raises_400() -> None:
    with pytest.raises(HTTPException) as ei:
        parse_metadata_filters([{"key": "x", "operator": "like", "value": "A"}])
    assert ei.value.status_code == 400


# ── JSON 字符串形式（GET query 参数）────────────────────────────────────────


def test_json_none_or_empty_returns_empty() -> None:
    assert parse_metadata_filters_json(None) == []
    assert parse_metadata_filters_json("") == []


def test_json_valid_parsed() -> None:
    out = parse_metadata_filters_json('[{"key":"year","operator":"gte","value":2023}]')
    assert out[0].operator == FilterOperator.GTE
    assert out[0].value == 2023


def test_json_malformed_raises_400() -> None:
    with pytest.raises(HTTPException) as ei:
        parse_metadata_filters_json("{not json")
    assert ei.value.status_code == 400


def test_json_non_list_raises_400() -> None:
    with pytest.raises(HTTPException) as ei:
        parse_metadata_filters_json('{"key":"a"}')
    assert ei.value.status_code == 400
