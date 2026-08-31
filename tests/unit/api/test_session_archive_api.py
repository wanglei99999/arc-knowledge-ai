from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import UserContext, require_user
from app.api.routers import session as session_router
from app.domain.memory import ArchivedSession
from app.services.session_service import (
    ArchivedSessionPage,
    SessionBusyError,
    SessionSpaceArchivedError,
)


class _Service:
    def __init__(self) -> None:
        self.archive_result = True
        self.restore_result = True
        self.error: Exception | None = None
        self.calls: list[tuple[str, tuple, dict]] = []

    async def archive(self, *args):
        self.calls.append(("archive", args, {}))
        if self.error:
            raise self.error
        return self.archive_result

    async def restore(self, *args):
        self.calls.append(("restore", args, {}))
        if self.error:
            raise self.error
        return self.restore_result

    async def list_archived(self, *args, **kwargs):
        self.calls.append(("list_archived", args, kwargs))
        return ArchivedSessionPage(
            items=[
                ArchivedSession(
                    session_id="session-1",
                    space_id="space-1",
                    space_name="产品文档",
                    space_status="active",
                    title="旧版上传流程",
                    message_count=2,
                    archived_at=datetime(2026, 8, 21, 9, 30, tzinfo=UTC),
                )
            ],
            total=1,
        )


@pytest.fixture
def api(monkeypatch):
    service = _Service()
    monkeypatch.setattr(session_router, "_service", service)
    app = FastAPI()
    app.include_router(session_router.router)
    app.dependency_overrides[require_user] = lambda: UserContext(
        tenant_id="tenant-1", user_id="user-1"
    )
    return TestClient(app, raise_server_exceptions=False), service


def test_archive_returns_204_and_uses_authenticated_owner(api) -> None:
    client, service = api

    response = client.post("/sessions/session-1/archive")

    assert response.status_code == 204
    assert service.calls == [(
        "archive",
        ("session-1", "tenant-1", "user-1"),
        {},
    )]


def test_archive_unknown_session_returns_404(api) -> None:
    client, service = api
    service.archive_result = False

    response = client.post("/sessions/missing/archive")

    assert response.status_code == 404
    assert service.calls == [(
        "archive",
        ("missing", "tenant-1", "user-1"),
        {},
    )]


def test_archive_busy_returns_structured_409(api) -> None:
    client, service = api
    service.error = SessionBusyError()

    response = client.post("/sessions/session-1/archive")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SESSION_BUSY"


def test_restore_archived_space_returns_structured_409(api) -> None:
    client, service = api
    service.error = SessionSpaceArchivedError()

    response = client.post("/sessions/session-1/restore")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SPACE_ARCHIVED"


def test_archived_list_uses_static_route_and_returns_space_summary(api) -> None:
    client, service = api

    response = client.get(
        "/sessions/archived?query=上传&space_id=space-1&limit=50&offset=0"
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "session_id": "session-1",
                "title": "旧版上传流程",
                "message_count": 2,
                "archived_at": "2026-08-21T09:30:00Z",
                "space": {
                    "space_id": "space-1",
                    "name": "产品文档",
                    "status": "active",
                },
            }
        ],
        "total": 1,
    }
    assert service.calls == [(
        "list_archived",
        ("tenant-1", "user-1"),
        {
            "query": "上传",
            "space_id": "space-1",
            "limit": 50,
            "offset": 0,
        },
    )]


def test_archived_list_rejects_invalid_pagination(api) -> None:
    client, service = api

    response = client.get("/sessions/archived?limit=101&offset=-1")

    assert response.status_code == 422
    assert service.calls == []
