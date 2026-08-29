from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.infrastructure.postgres.repositories import chunk_repo as repo_module
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository


class _Result:
    def __init__(self, row=None) -> None:
        self._row = row

    def one_or_none(self):
        return self._row


class _DB:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls = []

    async def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).lower().split()), params))
        return self.result


@pytest.mark.asyncio
async def test_reset_failed_document_is_conditional_and_clears_partial_state(
    monkeypatch,
) -> None:
    row = SimpleNamespace(_mapping={"id": "document-1"})
    db = _DB(_Result(row))

    @asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr(repo_module, "get_session", fake_session)

    changed = await ChunkRepository().reset_failed_document("document-1", "tenant-1")

    assert changed is True
    sql, params = db.calls[0]
    assert "status = 'pending'" in sql
    assert "chunk_count = 0" in sql
    assert "error_message = null" in sql
    assert "status = 'failed'" in sql
    assert "returning id" in sql
    assert params == {"document_id": "document-1", "tenant_id": "tenant-1"}
