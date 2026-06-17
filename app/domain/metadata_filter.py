from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# key 名白名单：仅字母/数字/下划线，防止拼入 Milvus 表达式时注入。
_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_SCALAR_TYPES = (bool, int, float, str)


class FilterOperator(StrEnum):
    """metadata 过滤操作符，对齐 LlamaIndex FilterOperator 语义。"""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NIN = "nin"


_LIST_OPERATORS = {FilterOperator.IN, FilterOperator.NIN}


@dataclass(frozen=True)
class MetadataFilter:
    """单条 metadata 过滤条件。"""

    key: str
    operator: FilterOperator
    value: Any


def parse_filters(raw: list[dict]) -> list[MetadataFilter]:
    """把请求中的过滤条件解析为 MetadataFilter 列表，并做安全校验。

    校验：key 名白名单（防表达式注入）、operator 白名单、value 类型
    （in/nin 要求非空 list）。任一不合法抛 ValueError（由 API 层转 400）。
    """
    filters: list[MetadataFilter] = []
    for item in raw:
        key = item.get("key")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise ValueError(f"非法 metadata filter key: {key!r}")

        try:
            operator = FilterOperator(item.get("operator"))
        except ValueError:
            raise ValueError(f"不支持的 operator: {item.get('operator')!r}") from None

        value = item.get("value")
        if operator in _LIST_OPERATORS:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{operator.value} 的 value 必须是非空 list")
            if not all(isinstance(v, _SCALAR_TYPES) for v in value):
                raise ValueError(f"{operator.value} 的 value 元素类型非法: {value!r}")
        elif not isinstance(value, _SCALAR_TYPES):
            raise ValueError(f"value 类型非法: {value!r}")

        filters.append(MetadataFilter(key=key, operator=operator, value=value))
    return filters
