"""API 边界的 metadata 过滤条件解析/校验。

把前端传入的原始过滤条件转成已校验的 MetadataFilter 列表；
非法输入统一抛 HTTP 400，避免把脏数据带入检索链路。
"""

from __future__ import annotations

import json

from fastapi import HTTPException

from app.domain.metadata_filter import MetadataFilter, parse_filters


def parse_metadata_filters(raw: list[dict] | None) -> list[MetadataFilter]:
    """校验过滤条件列表（POST body 形式）；非法时抛 HTTP 400。"""
    if not raw:
        return []
    try:
        return parse_filters(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"非法的过滤条件: {exc}") from exc


def parse_metadata_filters_json(raw: str | None) -> list[MetadataFilter]:
    """解析 JSON 字符串形式的过滤条件（GET query 参数）；非法时抛 HTTP 400。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"filters 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail="filters 必须是数组")
    return parse_metadata_filters(parsed)
