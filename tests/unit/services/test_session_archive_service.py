from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.memory import ArchivedSession, Session
from app.domain.space import Space
from app.services import session_service as service_module
from app.services.session_service import SessionService


class _SessionRepository:
    def __init__(self) -> None:
        self.owned: Session | None = Session(
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            space_id="space-1",
        )
        self.archive_result = True
        self.restore_result = True
        self.pin_result = True
        self.unpin_result = True
        self.archived_items = [
            ArchivedSession(
                session_id="session-1",
                space_id="space-1",
                space_name="产品文档",
                space_status="active",
                title="旧版上传流程",
                message_count=2,
                archived_at=datetime(2026, 8, 21, 9, 30, tzinfo=UTC),
            )
        ]
        self.calls: list[tuple[str, tuple, dict]] = []

    async def get_by_id(self, *args):
        self.calls.append(("get", args, {}))
        return self.owned

    async def archive(self, *args):
        self.calls.append(("archive", args, {}))
        return self.archive_result

    async def restore(self, *args):
        self.calls.append(("restore", args, {}))
        return self.restore_result

    async def pin(self, *args):
        self.calls.append(("pin", args, {}))
        return self.pin_result

    async def unpin(self, *args):
        self.calls.append(("unpin", args, {}))
        return self.unpin_result

    async def list_archived(self, *args, **kwargs):
        self.calls.append(("list_archived", args, kwargs))
        return self.archived_items, 1


class _TurnRepository:
    def __init__(self) -> None:
        self.active = False
        self.calls: list[tuple[str, str, str]] = []

    async def has_active_turn(self, session_id, tenant_id, user_id):
        self.calls.append((session_id, tenant_id, user_id))
        return self.active


class _SpaceRepository:
    def __init__(self) -> None:
        self.space: Space | None = Space(
            space_id="space-1",
            tenant_id="tenant-1",
            space_key="product-docs",
            name="产品文档",
            status="active",
        )
        self.calls: list[tuple[str, str]] = []

    async def get_by_id(self, tenant_id, space_id):
        self.calls.append((tenant_id, space_id))
        return self.space


class _UnusedRepository:
    pass


def _service():
    session_repo = _SessionRepository()
    turn_repo = _TurnRepository()
    space_repo = _SpaceRepository()
    service = SessionService(
        session_repo=session_repo,
        memory_repo=_UnusedRepository(),
        citation_repo=_UnusedRepository(),
        attachment_repo=turn_repo,
        space_repo=space_repo,
    )
    return service, session_repo, turn_repo, space_repo


@pytest.mark.asyncio
async def test_archive_unknown_owned_session_returns_false() -> None:
    service, session_repo, turn_repo, _ = _service()
    session_repo.owned = None

    result = await service.archive("missing", "tenant-1", "user-1")

    assert result is False
    assert turn_repo.calls == []


@pytest.mark.asyncio
async def test_archive_rejects_active_turn_without_mutating_session() -> None:
    service, session_repo, turn_repo, _ = _service()
    turn_repo.active = True

    with pytest.raises(Exception) as error:
        await service.archive("session-1", "tenant-1", "user-1")

    assert type(error.value).__name__ == "SessionBusyError"
    assert not any(name == "archive" for name, _, _ in session_repo.calls)


@pytest.mark.asyncio
async def test_archive_delegates_with_owned_scope() -> None:
    service, session_repo, turn_repo, _ = _service()

    result = await service.archive("session-1", "tenant-1", "user-1")

    assert result is True
    assert turn_repo.calls == [("session-1", "tenant-1", "user-1")]
    assert session_repo.calls[-1] == (
        "archive",
        ("session-1", "tenant-1", "user-1"),
        {},
    )


@pytest.mark.asyncio
async def test_restore_rejects_archived_space_without_mutating_session() -> None:
    service, session_repo, _, space_repo = _service()
    space_repo.space.status = "archived"

    with pytest.raises(Exception) as error:
        await service.restore("session-1", "tenant-1", "user-1")

    assert type(error.value).__name__ == "SessionSpaceArchivedError"
    assert not any(name == "restore" for name, _, _ in session_repo.calls)


@pytest.mark.asyncio
async def test_restore_unknown_owned_session_returns_false() -> None:
    service, session_repo, _, space_repo = _service()
    session_repo.owned = None

    result = await service.restore("missing", "tenant-1", "user-1")

    assert result is False
    assert space_repo.calls == []


@pytest.mark.asyncio
async def test_restore_delegates_after_loading_active_original_space() -> None:
    service, session_repo, _, space_repo = _service()

    result = await service.restore("session-1", "tenant-1", "user-1")

    assert result is True
    assert space_repo.calls == [("tenant-1", "space-1")]
    assert session_repo.calls[-1] == (
        "restore",
        ("session-1", "tenant-1", "user-1"),
        {},
    )


@pytest.mark.asyncio
async def test_pin_delegates_with_owned_scope() -> None:
    service, session_repo, _, _ = _service()

    result = await service.pin("session-1", "tenant-1", "user-1")

    assert result is True
    assert session_repo.calls[-1] == (
        "pin",
        ("session-1", "tenant-1", "user-1"),
        {},
    )


@pytest.mark.asyncio
async def test_unpin_delegates_with_owned_scope() -> None:
    service, session_repo, _, _ = _service()

    result = await service.unpin("session-1", "tenant-1", "user-1")

    assert result is True
    assert session_repo.calls[-1] == (
        "unpin",
        ("session-1", "tenant-1", "user-1"),
        {},
    )


@pytest.mark.asyncio
async def test_list_archived_returns_page_from_owned_repository_query() -> None:
    service, session_repo, _, _ = _service()

    page = await service.list_archived(
        "tenant-1",
        "user-1",
        query="上传",
        space_id="space-1",
        limit=50,
        offset=0,
    )

    assert page.total == 1
    assert page.items[0].space_name == "产品文档"
    assert session_repo.calls[-1] == (
        "list_archived",
        ("tenant-1", "user-1"),
        {
            "query": "上传",
            "space_id": "space-1",
            "limit": 50,
            "offset": 0,
        },
    )
