"""Milvus filter 表达式编译单元测试——纯函数，无外部依赖。"""

from app.domain.metadata_filter import FilterOperator, MetadataFilter
from app.infrastructure.milvus.filter_builder import build_milvus_expr


def _f(key: str, op: str, value) -> MetadataFilter:
    return MetadataFilter(key=key, operator=FilterOperator(op), value=value)


def test_empty_returns_empty_string() -> None:
    assert build_milvus_expr([]) == ""


def test_eq_string() -> None:
    assert build_milvus_expr([_f("project", "eq", "A")]) == 'metadata["project"] == "A"'


def test_eq_number() -> None:
    assert build_milvus_expr([_f("year", "eq", 2023)]) == 'metadata["year"] == 2023'


def test_ne() -> None:
    assert build_milvus_expr([_f("k", "ne", "x")]) == 'metadata["k"] != "x"'


def test_gte() -> None:
    assert build_milvus_expr([_f("year", "gte", 2023)]) == 'metadata["year"] >= 2023'


def test_lt() -> None:
    assert build_milvus_expr([_f("year", "lt", 2000)]) == 'metadata["year"] < 2000'


def test_in() -> None:
    assert build_milvus_expr([_f("status", "in", ["a", "b"])]) == 'metadata["status"] in ["a", "b"]'


def test_nin() -> None:
    assert build_milvus_expr([_f("status", "nin", ["a"])]) == 'metadata["status"] not in ["a"]'


def test_bool_value_lowercase() -> None:
    assert build_milvus_expr([_f("active", "eq", True)]) == 'metadata["active"] == true'


def test_multiple_and_joined() -> None:
    expr = build_milvus_expr([_f("p", "eq", "A"), _f("year", "gte", 2023)])
    assert expr == 'metadata["p"] == "A" and metadata["year"] >= 2023'


def test_string_value_escaped_against_injection() -> None:
    # 双引号必须被转义，否则破坏表达式 / 注入
    expr = build_milvus_expr([_f("k", "eq", 'a"b')])
    assert expr == 'metadata["k"] == "a\\"b"'


def test_backslash_escaped() -> None:
    expr = build_milvus_expr([_f("k", "eq", "a\\b")])
    assert expr == 'metadata["k"] == "a\\\\b"'
