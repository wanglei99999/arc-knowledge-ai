from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.infrastructure.postgres.repositories import session_repo as repo_module
from app.infrastructure.postgres.repositories.session_repo import SessionRepository


class _Result:
    def __init__(
        self,
        *,
        row=None,
        rows=None,
        rowcount: int = 0,
        scalar=None,
    ) -> None:
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount
        self._scalar = scalar

    def one_or_none(self):
        return self._row

    def fetchall(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _FakeDB:
    def __init__(self, responses: list[_Result] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement, params=None):
        self.calls.append((_normalize_sql(statement), params))
        if self.responses:
            return self.responses.pop(0)
        return _Result()


def _normalize_sql(statement) -> str:
    return " ".join(str(statement).lower().split())


def _row(**values):
    return SimpleNamespace(_mapping=values)


def _patch_session(monkeypatch, db: _FakeDB) -> None:
    @asynccontextmanager
    async def fake_get_session():
        yield db

    monkeypatch.setattr(repo_module, "get_db", fake_get_session)


@pytest.mark.asyncio
async def test_active_list_filters_archived_sessions(monkeypatch) -> None:
    db = _FakeDB([_Result(rows=[])])
    _patch_session(monkeypatch, db)

    await SessionRepository().list("tenant-1", "user-1", space_id="space-1")

    sql, params = db.calls[0]
    assert "archived_at is null" in sql
    assert params["tenant_id"] == "tenant-1"
    assert params["user_id"] == "user-1"
    assert params["space_id"] == "space-1"


@pytest.mark.asyncio
async def test_session_lookup_maps_archive_and_pin_timestamps(monkeypatch) -> None:
    archived_at = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)
    pinned_at = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
    session_row = _row(
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        space_id="space-1",
        title="旧会话",
        summary=None,
        message_count=2,
        archived_at=archived_at,
        pinned_at=pinned_at,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    db = _FakeDB([_Result(row=session_row)])
    _patch_session(monkeypatch, db)

    session = await SessionRepository().get_by_id(
        "session-1", "tenant-1", "user-1"
    )

    assert session is not None
    assert session.archived_at == archived_at
    assert session.pinned_at == pinned_at


@pytest.mark.asyncio
async def test_archive_is_owned_idempotent_and_clears_pin(monkeypatch) -> None:
    db = _FakeDB([_Result(rowcount=1)])
    _patch_session(monkeypatch, db)

    changed = await SessionRepository().archive(
        "session-1", "tenant-1", "user-1"
    )

    assert changed is True
    sql, params = db.calls[0]
    assert "archived_at = coalesce(archived_at, now())" in sql
    assert "pinned_at = null" in sql
    assert "tenant_id = :tenant_id" in sql
    assert "user_id = :user_id" in sql
    assert params == {
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
    }


@pytest.mark.asyncio
async def test_restore_is_owned_and_idempotent(monkeypatch) -> None:
    db = _FakeDB([_Result(rowcount=1)])
    _patch_session(monkeypatch, db)

    changed = await SessionRepository().restore(
        "session-1", "tenant-1", "user-1"
    )

    assert changed is True
    sql, params = db.calls[0]
    assert "set archived_at = null" in sql
    assert "tenant_id = :tenant_id" in sql
    assert "user_id = :user_id" in sql
    assert params == {
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
    }


@pytest.mark.asyncio
async def test_archived_query_joins_space_counts_and_maps_rows(monkeypatch) -> None:
    archived_at = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)
    archived_row = _row(
        session_id="session-1",
        space_id="space-1",
        space_name="产品文档",
        space_status="active",
        title="旧版上传流程",
        message_count=2,
        archived_at=archived_at,
    )
    db = _FakeDB([
        _Result(scalar=1),
        _Result(rows=[archived_row]),
    ])
    _patch_session(monkeypatch, db)

    items, total = await SessionRepository().list_archived(
        "tenant-1",
        "user-1",
        query="上传",
        space_id=None,
        limit=50,
        offset=0,
    )

    assert total == 1
    assert len(items) == 1
    assert items[0].session_id == "session-1"
    assert items[0].space_name == "产品文档"
    assert items[0].space_status == "active"
    assert items[0].archived_at == archived_at

    count_sql, count_params = db.calls[0]
    list_sql, list_params = db.calls[1]
    for sql in (count_sql, list_sql):
        assert "archived_at is not null" in sql
        assert "tenant_id = :tenant_id" in sql
        assert "user_id = :user_id" in sql
    assert "join spaces" in list_sql
    assert "lower(s.title) like :query" in list_sql
    assert "order by s.archived_at desc" in list_sql
    assert count_params["query"] == "%上传%"
    assert list_params["limit"] == 50
    assert list_params["offset"] == 0


@pytest.mark.asyncio
async def test_archived_query_filters_by_original_space(monkeypatch) -> None:
    db = _FakeDB([_Result(scalar=0), _Result(rows=[])])
    _patch_session(monkeypatch, db)

    await SessionRepository().list_archived(
        "tenant-1",
        "user-1",
        query=None,
        space_id="space-1",
        limit=20,
        offset=20,
    )

    for sql, params in db.calls:
        assert "s.space_id = :space_id" in sql
        assert params["space_id"] == "space-1"
