from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.infrastructure.postgres.repositories import chat_turn_repo as turn_module
from app.infrastructure.postgres.repositories import space_repo as space_module
from app.infrastructure.postgres.repositories.chat_turn_repo import ChatTurnRepository
from app.infrastructure.postgres.repositories.space_repo import SpaceRepository


class _Result:
    def __init__(self, *, row=None, scalar=None, rowcount: int = 0) -> None:
        self._row = row
        self._scalar = scalar
        self.rowcount = rowcount

    def one_or_none(self):
        return self._row

    def scalar_one(self):
        return self._scalar


class _FakeDB:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.calls.append((" ".join(str(statement).lower().split()), params))
        return self.result

    async def commit(self):
        self.commits += 1


def _patch_db(monkeypatch, module, db: _FakeDB) -> None:
    @asynccontextmanager
    async def fake_get_session():
        yield db

    monkeypatch.setattr(module, "get_session", fake_get_session)


@pytest.mark.asyncio
async def test_active_turn_query_is_owned_and_covers_both_busy_states(
    monkeypatch,
) -> None:
    db = _FakeDB(_Result(scalar=True))
    _patch_db(monkeypatch, turn_module, db)

    active = await ChatTurnRepository().has_active_turn("session-1", "tenant-1", "user-1")

    assert active is True
    sql, params = db.calls[0]
    assert "session_id = :session_id" in sql
    assert "tenant_id = :tenant_id" in sql
    assert "user_id = :user_id" in sql
    assert "role = 'user'" in sql
    assert "processing_status in ('waiting_files', 'answering')" in sql
    assert params == {
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
    }


@pytest.mark.asyncio
async def test_space_lookup_is_tenant_scoped_and_maps_archived_status(
    monkeypatch,
) -> None:
    db = _FakeDB(
        _Result(
            row=SimpleNamespace(
                _mapping={
                    "id": "space-1",
                    "tenant_id": "tenant-1",
                    "space_key": "product-docs",
                    "name": "产品文档",
                    "status": "archived",
                    "created_by": "user-1",
                    "created_at": datetime(2026, 8, 1, tzinfo=UTC),
                }
            )
        )
    )
    _patch_db(monkeypatch, space_module, db)

    space = await SpaceRepository().get_by_id("tenant-1", "space-1")

    assert space is not None
    assert space.status == "archived"
    sql, params = db.calls[0]
    assert "id = :space_id" in sql
    assert "tenant_id = :tenant_id" in sql
    assert params == {"tenant_id": "tenant-1", "space_id": "space-1"}


@pytest.mark.asyncio
async def test_space_restore_is_tenant_scoped_and_idempotent(monkeypatch) -> None:
    db = _FakeDB(_Result(rowcount=1))
    _patch_db(monkeypatch, space_module, db)

    restored = await SpaceRepository().restore("tenant-1", "space-1")

    assert restored is True
    sql, params = db.calls[0]
    assert "set status = 'active', updated_at = now()" in sql
    assert "id = :space_id" in sql
    assert "tenant_id = :tenant_id" in sql
    assert "status = 'archived'" not in sql
    assert params == {"tenant_id": "tenant-1", "space_id": "space-1"}
    assert db.commits == 1
