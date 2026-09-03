from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import UserContext, require_user
from app.api.routers import session as session_router
from app.domain.memory import ArchivedSession, Session
from app.services.session_service import (
    ArchivedSessionPage,
    SessionBusyError,
    SessionListResult,
    SessionSpaceArchivedError,
)


class _Service:
    def __init__(self) -> None:
        self.archive_result = True
        self.restore_result = True
        self.pin_result = Session(
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            space_id="space-1",
            title="接入鉴权方案",
            pinned_at=datetime(2026, 9, 1, 2, 30, tzinfo=UTC),
        )
        self.unpin_result = Session(
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            space_id="space-1",
            title="接入鉴权方案",
            pinned_at=None,
        )
        self.rename_result = Session(
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            space_id="space-1",
            title="新的标题",
        )
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

    async def pin(self, *args):
        self.calls.append(("pin", args, {}))
        return self.pin_result

    async def unpin(self, *args):
        self.calls.append(("unpin", args, {}))
        return self.unpin_result

    async def rename(self, *args):
        self.calls.append(("rename", args, {}))
        return self.rename_result

    async def list(self, *args, **kwargs):
        self.calls.append(("list", args, kwargs))
        return SessionListResult(
            sessions=[
                Session(
                    session_id="session-1",
                    tenant_id="tenant-1",
                    user_id="user-1",
                    space_id="space-1",
                    title="接入鉴权方案",
                    pinned_at=datetime(2026, 9, 1, 2, 30, tzinfo=UTC),
                    created_at=datetime(2026, 8, 20, 1, 0, tzinfo=UTC),
                    updated_at=datetime(2026, 8, 31, 9, 15, tzinfo=UTC),
                )
            ],
            total=1,
        )

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
    assert service.calls == [
        (
            "archive",
            ("session-1", "tenant-1", "user-1"),
            {},
        )
    ]


def test_archive_unknown_session_returns_404(api) -> None:
    client, service = api
    service.archive_result = False

    response = client.post("/sessions/missing/archive")

    assert response.status_code == 404
    assert service.calls == [
        (
            "archive",
            ("missing", "tenant-1", "user-1"),
            {},
        )
    ]


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


def test_pin_and_unpin_return_authoritative_state_with_authenticated_owner(api) -> None:
    client, service = api

    pin_response = client.post("/sessions/session-1/pin")
    unpin_response = client.post("/sessions/session-1/unpin")

    assert pin_response.status_code == 200
    assert pin_response.json()["pinned_at"] == "2026-09-01T02:30:00Z"
    assert unpin_response.status_code == 200
    assert unpin_response.json()["pinned_at"] is None
    assert service.calls == [
        ("pin", ("session-1", "tenant-1", "user-1"), {}),
        ("unpin", ("session-1", "tenant-1", "user-1"), {}),
    ]


def test_pin_unknown_or_archived_session_returns_404(api) -> None:
    client, service = api
    service.pin_result = None

    response = client.post("/sessions/missing/pin")

    assert response.status_code == 404


def test_rename_trims_title_and_returns_authoritative_session(api) -> None:
    client, service = api

    response = client.patch("/sessions/session-1", json={"title": "  新的标题  "})

    assert response.status_code == 200
    assert response.json()["title"] == "新的标题"
    assert service.calls == [
        (
            "rename",
            ("session-1", "tenant-1", "user-1", "新的标题"),
            {},
        )
    ]


@pytest.mark.parametrize("title", ["   ", "x" * 101])
def test_rename_rejects_invalid_title_without_calling_service(api, title) -> None:
    client, service = api

    response = client.patch("/sessions/session-1", json={"title": title})

    assert response.status_code == 422
    assert service.calls == []


def test_rename_unknown_or_archived_session_returns_404(api) -> None:
    client, service = api
    service.rename_result = None

    response = client.patch("/sessions/missing", json={"title": "新的标题"})

    assert response.status_code == 404


def test_active_session_list_exposes_pin_timestamp(api) -> None:
    client, _ = api

    response = client.get("/sessions?space_id=space-1")

    assert response.status_code == 200
    assert response.json()[0]["pinned_at"] == "2026-09-01T02:30:00Z"
    assert response.json()[0]["created_at"] == "2026-08-20T01:00:00Z"
    assert response.json()[0]["updated_at"] == "2026-08-31T09:15:00Z"


def test_archived_list_uses_static_route_and_returns_space_summary(api) -> None:
    client, service = api

    response = client.get("/sessions/archived?query=上传&space_id=space-1&limit=50&offset=0")

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
    assert service.calls == [
        (
            "list_archived",
            ("tenant-1", "user-1"),
            {
                "query": "上传",
                "space_id": "space-1",
                "limit": 50,
                "offset": 0,
            },
        )
    ]


def test_archived_list_rejects_invalid_pagination(api) -> None:
    client, service = api

    response = client.get("/sessions/archived?limit=101&offset=-1")

    assert response.status_code == 422
    assert service.calls == []
