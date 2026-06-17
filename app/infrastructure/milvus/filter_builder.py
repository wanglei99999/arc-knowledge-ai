from __future__ import annotations

from typing import Any

from app.domain.metadata_filter import FilterOperator, MetadataFilter

_COMPARISON_SYMBOL = {
    FilterOperator.EQ: "==",
    FilterOperator.NE: "!=",
    FilterOperator.GT: ">",
    FilterOperator.GTE: ">=",
    FilterOperator.LT: "<",
    FilterOperator.LTE: "<=",
}


def build_milvus_expr(filters: list[MetadataFilter]) -> str:
    """编译成 Milvus boolean 表达式（多条件 AND）。空列表返回空串。

    value 经类型化渲染，字符串转义双引号/反斜杠，防止破坏表达式或注入。
    （key 已在 parse_filters 经白名单校验，此处不再转义。）
    """
    return " and ".join(_build_one(f) for f in filters)


def _build_one(f: MetadataFilter) -> str:
    field = f'metadata["{f.key}"]'
    if f.operator is FilterOperator.IN:
        return f"{field} in {_render_list(f.value)}"
    if f.operator is FilterOperator.NIN:
        return f"{field} not in {_render_list(f.value)}"
    return f"{field} {_COMPARISON_SYMBOL[f.operator]} {_render_scalar(f.value)}"


def _render_scalar(v: Any) -> str:
    # bool 必须先于 int 判断（isinstance(True, int) 为 True）
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_list(values: list) -> str:
    return "[" + ", ".join(_render_scalar(v) for v in values) + "]"
