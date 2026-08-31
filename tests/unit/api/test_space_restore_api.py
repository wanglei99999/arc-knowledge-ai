from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import UserContext, require_user
from app.api.routers import spaces as spaces_router


class _Service:
    def __init__(self) -> None:
        self.result = True
        self.calls: list[tuple[str, str]] = []

    async def restore_space(self, tenant_id: str, space_id: str) -> bool:
        self.calls.append((tenant_id, space_id))
        return self.result


@pytest.fixture
def api(monkeypatch):
    service = _Service()
    monkeypatch.setattr(spaces_router, "_service", service)
    app = FastAPI()
    app.include_router(spaces_router.router)
    app.dependency_overrides[require_user] = lambda: UserContext(
        tenant_id="tenant-1", user_id="user-1"
    )
    return TestClient(app, raise_server_exceptions=False), service


def test_restore_space_returns_204_with_authenticated_tenant(api) -> None:
    client, service = api

    response = client.post("/spaces/space-1/restore")

    assert response.status_code == 204
    assert service.calls == [("tenant-1", "space-1")]


def test_restore_unknown_space_returns_404_after_owned_lookup(api) -> None:
    client, service = api
    service.result = False

    response = client.post("/spaces/missing/restore")

    assert response.status_code == 404
    assert service.calls == [("tenant-1", "missing")]
