from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.dependencies import UserContext, require_user
from app.api.routers import spaces as spaces_router
from app.domain.authorization import AuthorizationError

SPACE_ID = "33333333-3333-4333-8333-333333333333"


class _Service:
    def __init__(self) -> None:
        self.result = True
        self.calls: list[tuple[str, str]] = []

    async def update_space(self, ctx, space_id: str, *, status):
        assert status == "active"
        self.calls.append((ctx.tenant_id, space_id))
        if not self.result:
            raise AuthorizationError(404, "RESOURCE_NOT_FOUND", "Space not found")


@pytest.fixture
def api(monkeypatch):
    service = _Service()
    monkeypatch.setattr(spaces_router, "service", service)
    app = FastAPI()
    app.include_router(spaces_router.router)

    @app.exception_handler(AuthorizationError)
    async def forbidden(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    app.dependency_overrides[require_user] = lambda: UserContext(
        tenant_id="tenant-1", user_id="user-1"
    )
    return TestClient(app, raise_server_exceptions=False), service


def test_restore_space_returns_204_with_authenticated_tenant(api) -> None:
    client, service = api

    response = client.post(f"/spaces/{SPACE_ID}/restore")

    assert response.status_code == 204
    assert service.calls == [("tenant-1", SPACE_ID)]


def test_restore_unknown_space_returns_404_after_owned_lookup(api) -> None:
    client, service = api
    service.result = False

    response = client.post(f"/spaces/{SPACE_ID}/restore")

    assert response.status_code == 404
    assert service.calls == [("tenant-1", SPACE_ID)]
