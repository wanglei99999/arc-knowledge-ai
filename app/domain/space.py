from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Space:
    """知识空间领域模型"""
    space_id: str
    tenant_id: str
    space_key: str       # URL 友好标识，如 'default'、'product-docs'，租户内唯一
    name: str
    status: str = "active"          # active | archived
    created_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))