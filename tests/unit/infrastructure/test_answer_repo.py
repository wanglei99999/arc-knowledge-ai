from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

TURN_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"
SPACE_ID = "33333333-3333-4333-8333-333333333333"
DOC_ID = "44444444-4444-4444-8444-444444444444"
CHUNK_ID = "55555555-5555-4555-8555-555555555555"


class _Result:
    def __init__(self, row=None) -> None:
        self._row = row

    def one_or_none(self):
        return self._row


class _FakeDB:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[str, object]] = []
        self.context_entries = 0
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).lower().split())
        self.calls.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("database write failed")
        if "insert into messages" in sql:
            return _Result(SimpleNamespace(_mapping={
                **params,
                "created_at": datetime(2026, 8, 29, tzinfo=UTC),
            }))
        return _Result(SimpleNamespace(_mapping={"ok": True}))


def _patch_db(monkeypatch, module, db: _FakeDB) -> None:
    @asynccontextmanager
    async def fake_get_db():
        db.context_entries += 1
        try:
            yield db
        except Exception:
            db.rolled_back = True
            raise
        else:
            db.committed = True

    monkeypatch.setattr(module, "get_db", fake_get_db)


async def test_persist_answer_writes_all_critical_state_in_one_transaction(
    monkeypatch,
) -> None:
    from app.infrastructure.postgres.repositories import answer_repo as module
    from app.infrastructure.postgres.repositories.answer_repo import AnswerRepository

    db = _FakeDB()
    _patch_db(monkeypatch, module, db)
    citations = [{
        "doc_id": DOC_ID,
        "chunk_id": CHUNK_ID,
        "doc_name": "contract.pdf",
        "content": "合同金额为 100 万元",
        "score": 0.91,
        "source": "hybrid",
    }]

    message = await AnswerRepository().save_assistant_with_citations(
        source_turn_id=TURN_ID,
        session_id=SESSION_ID,
        tenant_id="tenant-1",
        user_id="user-1",
        content="合同金额为 100 万元。",
        space_id=SPACE_ID,
        citations=citations,
    )

    assert message.role == "assistant"
    assert message.content == "合同金额为 100 万元。"
    assert db.context_entries == 1
    assert db.committed is True
    assert db.rolled_back is False
    assert [
        next(
            name
            for marker, name in [
                ("insert into messages", "assistant"),
                ("insert into message_citations", "citations"),
                ("update sessions", "session_count"),
                ("set processing_status = 'completed'", "completed"),
            ]
            if marker in sql
        )
        for sql, _ in db.calls
    ] == ["assistant", "citations", "session_count", "completed"]
    citation_params = db.calls[1][1]
    assert citation_params[0]["document_id"] == DOC_ID
    assert citation_params[0]["chunk_id"] == CHUNK_ID
    completed_sql, completed_params = db.calls[3]
    assert "processing_status = 'answering'" in completed_sql
    assert "m.tenant_id = :tenant_id" in completed_sql
    assert completed_params["source_turn_id"] == TURN_ID


async def test_persist_answer_rolls_back_when_citation_insert_fails(
    monkeypatch,
) -> None:
    from app.infrastructure.postgres.repositories import answer_repo as module
    from app.infrastructure.postgres.repositories.answer_repo import AnswerRepository

    db = _FakeDB(fail_on="insert into message_citations")
    _patch_db(monkeypatch, module, db)

    with pytest.raises(RuntimeError, match="database write failed"):
        await AnswerRepository().save_assistant_with_citations(
            source_turn_id=TURN_ID,
            session_id=SESSION_ID,
            tenant_id="tenant-1",
            user_id="user-1",
            content="回答",
            space_id=SPACE_ID,
            citations=[{
                "doc_id": DOC_ID,
                "chunk_id": CHUNK_ID,
                "doc_name": "contract.pdf",
                "content": "原文",
                "score": 0.8,
                "source": "vector",
            }],
        )

    assert db.context_entries == 1
    assert db.committed is False
    assert db.rolled_back is True
    assert len(db.calls) == 2
