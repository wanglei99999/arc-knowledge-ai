"""CRITICAL-1 回归：文档端点必须经 require_tenant 鉴权。

生产环境下，仅凭伪造的 X-Tenant-Id（无合法 JWT）必须被 401 拒绝，
绝不允许进入业务逻辑（越权增删查租户文档）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.infrastructure.postgres.repositories.chunk_repo as chunk_repo_mod
from app.api.routers import document
from app.config.settings import settings


class _FakeRepo:
    async def create_document(self, **kwargs):
        return None

    async def get_chunks_by_document(self, document_id, tenant_id):
        return []


class _FakeService:
    async def ingest(self, req):
        from app.services.document_service import IngestResult

        return IngestResult(document_id="d", task_id="t", workflow_run_id="r")

    async def get_status(self, *args, **kwargs):
        return {"document_id": "d", "workflow_status": "RUNNING"}

    async def list_documents(self, *args, **kwargs):
        return {"items": [], "total": 0}

    async def delete(self, *args, **kwargs):
        from app.services.document_service import DeleteResult

        return DeleteResult(document_id="d")


@pytest.fixture
def client(monkeypatch):
    # 生产环境：X-Tenant-Id 降级被禁用，只认 JWT
    monkeypatch.setattr(settings, "app_env", "production")
    # 用 fake 替换所有 IO，使（未修复前）端点能快速返回 2xx，从而让"应为 401"清晰失败
    monkeypatch.setattr(document, "_service", _FakeService())
    monkeypatch.setattr(document, "ChunkRepository", _FakeRepo)
    monkeypatch.setattr(chunk_repo_mod, "ChunkRepository", _FakeRepo)

    async def _fake_upload(*args, **kwargs):
        return None

    monkeypatch.setattr(document, "upload_file", _fake_upload)
    monkeypatch.setattr(document, "build_object_key", lambda *a, **k: "key")

    app = FastAPI()
    app.include_router(document.router)
    return TestClient(app, raise_server_exceptions=False)


_FORGED = {"X-Tenant-Id": "victim-tenant"}
_CASES = [
    (
        "post",
        "/documents/upload",
        {"params": {"space_id": "s1"}, "files": {"file": ("a.txt", b"x")}},
    ),
    ("get", "/documents/doc-1/status", {}),
    ("get", "/documents", {}),
    ("get", "/documents/doc-1/chunks", {}),
    ("delete", "/documents/doc-1", {}),
]


@pytest.mark.parametrize("method,path,kwargs", _CASES)
def test_endpoint_rejects_forged_tenant_in_production(client, method, path, kwargs):
    resp = client.request(method, path, headers=_FORGED, **kwargs)
    assert resp.status_code == 401, f"{method} {path} 未拒绝伪造租户：得到 {resp.status_code}"
