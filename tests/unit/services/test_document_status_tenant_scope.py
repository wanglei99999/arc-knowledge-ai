"""CRITICAL-1 回归：get_status 必须按 tenant 校验文档归属。

此前 get_status(document_id) 不带 tenant_id，已登录租户可凭 UUID 跨租户
查询他人文档状态（IDOR）。修复后必须先校验归属，不属于则拒绝（且不触达 Temporal）。
"""

from __future__ import annotations

import pytest

import app.services.document_service as document_service
from app.services.document_service import DocumentService


async def test_get_status_rejects_document_of_other_tenant(monkeypatch):
    class _Repo:
        async def get_document_meta(self, document_id, tenant_id):
            # 文档不属于该租户 → 视为不存在
            return None

    monkeypatch.setattr(document_service, "ChunkRepository", _Repo)

    # 若归属校验缺失而去连 Temporal，则此处会触发；归属校验应抢先拦截
    async def _boom():
        raise AssertionError("不应触达 Temporal —— 归属校验应先拒绝")

    monkeypatch.setattr(DocumentService, "_get_temporal_client", lambda self: _boom())

    svc = DocumentService()
    with pytest.raises(ValueError):
        await svc.get_status("doc-1", "attacker-tenant")
