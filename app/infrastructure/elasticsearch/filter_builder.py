from __future__ import annotations

from app.domain.metadata_filter import FilterOperator, MetadataFilter

_RANGE_KEY = {
    FilterOperator.GT: "gt",
    FilterOperator.GTE: "gte",
    FilterOperator.LT: "lt",
    FilterOperator.LTE: "lte",
}


def build_es_clauses(filters: list[MetadataFilter]) -> list[dict]:
    """编译成 ES bool filter 子句列表。空列表返回空 list。

    ES 用结构化 query dict，value 由 ES 客户端安全序列化，无注入风险。
    """
    return [_build_one(f) for f in filters]


def _build_one(f: MetadataFilter) -> dict:
    field = f"metadata.{f.key}"
    op = f.operator
    if op is FilterOperator.EQ:
        return {"term": {field: f.value}}
    if op is FilterOperator.NE:
        return {"bool": {"must_not": [{"term": {field: f.value}}]}}
    if op is FilterOperator.IN:
        return {"terms": {field: f.value}}
    if op is FilterOperator.NIN:
        return {"bool": {"must_not": [{"terms": {field: f.value}}]}}
    return {"range": {field: {_RANGE_KEY[op]: f.value}}}
