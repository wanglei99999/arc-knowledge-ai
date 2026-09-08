from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.domain.authorization import UserContext
from app.domain.document import DocumentStatus
from app.infrastructure.postgres.repositories import chunk_repo


@pytest.mark.parametrize("audit_fails", [False, True])
async def test_document_delete_attribution_and_audit_share_transaction(monkeypatch, audit_fails):
    committed = []
    pending = []

    class Database:
        async def execute(self, sql, params):
            if "INSERT INTO audit_events" in str(sql) and audit_fails:
                raise RuntimeError("audit unavailable")
            pending.append((str(sql), params))
            return SimpleNamespace(rowcount=1)

    @asynccontextmanager
    async def transaction():
        try:
            yield Database()
        except Exception:
            pending.clear()
            raise
        else:
            committed.extend(pending)

    monkeypatch.setattr(chunk_repo, "get_session", transaction)
    ctx = UserContext("tenant", "user", "member")
    operation = chunk_repo.ChunkRepository().update_document_status(
        "doc", "tenant", DocumentStatus.DELETED, actor=ctx
    )
    if audit_fails:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await operation
        assert committed == []
    else:
        await operation
        assert len(committed) == 2
        assert committed[0][1]["deleted_by"] == "user"
        assert committed[1][1]["actor"] == "member"
        assert committed[1][1]["action"] == "document.delete"
