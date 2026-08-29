from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.chat_turn import AttachmentDeclaration, AttachmentStatus, TurnReadiness
from app.infrastructure.postgres.repositories import chat_turn_repo as repo_module
from app.infrastructure.postgres.repositories import session_repo as session_repo_module
from app.infrastructure.postgres.repositories.chat_turn_repo import ChatTurnRepository
from app.infrastructure.postgres.repositories.session_repo import SessionRepository


class _Result:
    def __init__(self, *, row=None, rows=None, rowcount: int = 0) -> None:
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def one_or_none(self):
        return self._row

    def one(self):
        if self._row is None:
            raise AssertionError("expected one row")
        return self._row

    def fetchall(self):
        return self._rows


class _FakeDB:
    def __init__(self, responses: list[_Result] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, object]] = []
        self.context_entries = 0

    async def execute(self, statement, params=None):
        self.calls.append((_normalize_sql(statement), params))
        if self.responses:
            return self.responses.pop(0)
        return _Result()


def _normalize_sql(statement) -> str:
    return " ".join(str(statement).lower().split())


def _row(**values):
    return SimpleNamespace(_mapping=values)


def _patch_session(monkeypatch, module, attribute: str, db: _FakeDB) -> None:
    @asynccontextmanager
    async def fake_get_session():
        db.context_entries += 1
        yield db

    monkeypatch.setattr(module, attribute, fake_get_session)


def _turn_row(**overrides):
    values = {
        "message_id": "turn-1",
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "space_id": "space-1",
        "content": "总结附件",
        "processing_status": "waiting_files",
        "processing_error": None,
        "created_at": datetime(2026, 8, 28, tzinfo=UTC),
    }
    values.update(overrides)
    return _row(**values)


def _attachment_row(**overrides):
    values = {
        "attachment_id": "attachment-1",
        "client_id": "client-1",
        "file_name": "contract.pdf",
        "mime_type": "application/pdf",
        "file_size": 1024,
        "document_id": None,
        "upload_status": "pending",
        "upload_error": None,
        "ignored": False,
        "document_status": None,
        "document_error": None,
    }
    values.update(overrides)
    return _row(**values)


def _hydration_results(*attachments) -> list[_Result]:
    return [
        _Result(row=_turn_row()),
        _Result(rows=list(attachments)),
        _Result(row=None),
    ]


@pytest.mark.asyncio
async def test_create_turn_reuses_client_request_id(monkeypatch) -> None:
    db = _FakeDB([
        _Result(row=_row(session_id="session-1")),
        _Result(row=_row(message_id="turn-1")),
        *_hydration_results(_attachment_row()),
    ])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    turn = await ChatTurnRepository().create_turn(
        client_request_id="request-1",
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        space_id="space-1",
        query="总结附件",
        attachments=[
            AttachmentDeclaration("client-1", "contract.pdf", "application/pdf", 1024)
        ],
    )

    assert turn.turn_id == "turn-1"
    assert db.context_entries == 1
    assert not any("insert into messages" in sql for sql, _ in db.calls)
    idempotency_sql, params = db.calls[1]
    assert "client_request_id = :client_request_id" in idempotency_sql
    assert "tenant_id = :tenant_id" in idempotency_sql
    assert "user_id = :user_id" in idempotency_sql
    assert params["client_request_id"] == "request-1"


@pytest.mark.asyncio
async def test_create_turn_inserts_message_and_placeholders_in_one_session(monkeypatch) -> None:
    db = _FakeDB([
        _Result(row=_row(session_id="session-1")),
        _Result(row=None),
        _Result(),
        _Result(),
        _Result(),
        *_hydration_results(_attachment_row()),
    ])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    turn = await ChatTurnRepository().create_turn(
        client_request_id="request-1",
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        space_id="space-1",
        query="总结附件",
        attachments=[
            AttachmentDeclaration("client-1", "contract.pdf", "application/pdf", 1024)
        ],
    )

    assert turn.processing_status.value == "waiting_files"
    assert db.context_entries == 1
    message_call = next(call for call in db.calls if "insert into messages" in call[0])
    attachment_call = next(
        call
        for call in db.calls
        if "insert into message_attachments" in call[0]
    )
    assert message_call[1]["client_request_id"] == "request-1"
    assert message_call[1]["processing_status"] == "waiting_files"
    assert attachment_call[1][0]["client_id"] == "client-1"


@pytest.mark.asyncio
async def test_get_turn_rejects_other_tenant_or_user(monkeypatch) -> None:
    db = _FakeDB([_Result(row=None)])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    turn = await ChatTurnRepository().get_turn(
        turn_id="turn-1",
        tenant_id="tenant-1",
        user_id="other-user",
    )

    assert turn is None
    sql, params = db.calls[0]
    assert "m.tenant_id = :tenant_id" in sql
    assert "m.user_id = :user_id" in sql
    assert "s.user_id = :user_id" in sql
    assert params == {
        "turn_id": "turn-1",
        "tenant_id": "tenant-1",
        "user_id": "other-user",
    }


@pytest.mark.asyncio
async def test_get_turn_maps_upload_and_document_states(monkeypatch) -> None:
    db = _FakeDB(_hydration_results(
        _attachment_row(),
        _attachment_row(
            attachment_id="attachment-2",
            client_id="client-2",
            document_id="document-2",
            upload_status="uploaded",
            document_status="indexed",
        ),
        _attachment_row(
            attachment_id="attachment-3",
            client_id="client-3",
            upload_status="failed",
            upload_error="上传中断",
        ),
        _attachment_row(
            attachment_id="attachment-4",
            client_id="client-4",
            ignored=True,
        ),
    ))
    _patch_session(monkeypatch, repo_module, "get_session", db)

    turn = await ChatTurnRepository().get_turn(
        turn_id="turn-1", tenant_id="tenant-1", user_id="user-1"
    )

    assert turn is not None
    assert [item.status for item in turn.attachments] == [
        AttachmentStatus.PENDING_UPLOAD,
        AttachmentStatus.INDEXED,
        AttachmentStatus.FAILED,
        AttachmentStatus.IGNORED,
    ]
    assert turn.attachments[2].error_message == "上传中断"
    assert turn.readiness is TurnReadiness.BLOCKED
    attachment_sql, _ = db.calls[1]
    assert "left join documents" in attachment_sql
    assert "d.status as document_status" in attachment_sql


@pytest.mark.asyncio
async def test_link_document_is_idempotent(monkeypatch) -> None:
    db = _FakeDB([_Result(row=_row(attachment_id="attachment-1"))])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    linked = await ChatTurnRepository().link_document(
        turn_id="turn-1",
        attachment_id="attachment-1",
        document_id="document-1",
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert linked is True
    sql, params = db.calls[0]
    assert "document_id is null or ma.document_id = :document_id" in sql
    assert "m.tenant_id = :tenant_id" in sql
    assert "m.user_id = :user_id" in sql
    assert params["document_id"] == "document-1"


@pytest.mark.asyncio
async def test_mark_upload_failed_records_error_for_owner(monkeypatch) -> None:
    db = _FakeDB([_Result(row=_row(attachment_id="attachment-1"))])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    changed = await ChatTurnRepository().mark_upload_failed(
        turn_id="turn-1",
        attachment_id="attachment-1",
        tenant_id="tenant-1",
        user_id="user-1",
        error_message="连接中断",
    )

    assert changed is True
    sql, params = db.calls[0]
    assert "upload_status = 'failed'" in sql
    assert "m.user_id = :user_id" in sql
    assert params["error_message"] == "连接中断"


@pytest.mark.asyncio
async def test_mark_upload_failed_does_not_overwrite_linked_attachment(monkeypatch) -> None:
    db = _FakeDB([_Result(row=None)])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    changed = await ChatTurnRepository().mark_upload_failed(
        turn_id="turn-1",
        attachment_id="attachment-1",
        tenant_id="tenant-1",
        user_id="user-1",
        error_message="迟到的失败回调",
    )

    assert changed is False
    sql, _ = db.calls[0]
    assert "ma.document_id is null" in sql
    assert "ma.upload_status in ('pending', 'failed')" in sql


@pytest.mark.asyncio
async def test_set_ignored_updates_only_owned_attachment(monkeypatch) -> None:
    db = _FakeDB([_Result(row=_row(attachment_id="attachment-1"))])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    changed = await ChatTurnRepository().set_ignored(
        turn_id="turn-1",
        attachment_id="attachment-1",
        tenant_id="tenant-1",
        user_id="user-1",
        ignored=True,
    )

    assert changed is True
    sql, params = db.calls[0]
    assert "ignored = :ignored" in sql
    assert "m.tenant_id = :tenant_id" in sql
    assert "m.user_id = :user_id" in sql
    assert params["ignored"] is True


@pytest.mark.asyncio
async def test_add_attachment_locks_owned_turn_and_enforces_limit(monkeypatch) -> None:
    db = _FakeDB([
        _Result(row=_row(message_id="turn-1", space_id="space-1")),
        _Result(row=None),
        _Result(row=_row(attachment_count=4)),
        _Result(row=_row(attachment_id="attachment-5")),
    ])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    added = await ChatTurnRepository().add_attachment(
        turn_id="turn-1",
        tenant_id="tenant-1",
        user_id="user-1",
        attachment=AttachmentDeclaration(
            "client-5", "appendix.pdf", "application/pdf", 2048
        ),
        max_attachments=5,
    )

    assert added is True
    lock_sql, lock_params = db.calls[0]
    assert "for update" in lock_sql
    assert "m.processing_status = 'waiting_files'" in lock_sql
    assert "m.user_id = :user_id" in lock_sql
    assert lock_params["user_id"] == "user-1"
    count_sql, _ = db.calls[2]
    assert "count(*)" in count_sql
    insert_sql, insert_params = db.calls[3]
    assert "insert into message_attachments" in insert_sql
    assert insert_params["space_id"] == "space-1"
    assert insert_params["client_id"] == "client-5"


@pytest.mark.asyncio
async def test_claim_answer_updates_only_waiting_or_failed_turn(monkeypatch) -> None:
    db = _FakeDB([
        _Result(row=_row(message_id="turn-1")),
        _Result(row=None),
    ])
    _patch_session(monkeypatch, repo_module, "get_session", db)
    repository = ChatTurnRepository()

    first_claim = await repository.claim_answer(
        turn_id="turn-1", tenant_id="tenant-1", user_id="user-1"
    )
    duplicate_claim = await repository.claim_answer(
        turn_id="turn-1", tenant_id="tenant-1", user_id="user-1"
    )

    assert first_claim is True
    assert duplicate_claim is False
    sql, _ = db.calls[0]
    assert "processing_status in ('waiting_files', 'answer_failed')" in sql
    assert "s.user_id = :user_id" in sql


@pytest.mark.asyncio
async def test_claim_ready_answer_atomically_checks_readiness_and_returns_scope(
    monkeypatch,
) -> None:
    doc1 = "11111111-1111-4111-8111-111111111111"
    doc2 = "22222222-2222-4222-8222-222222222222"
    db = _FakeDB([
        _Result(row=_row(
            message_id="turn-1",
            session_id="session-1",
            space_id="33333333-3333-4333-8333-333333333333",
            content="总结附件",
        )),
        _Result(rows=[_row(document_id=doc1), _row(document_id=doc2)]),
    ])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    claim = await ChatTurnRepository().claim_ready_answer(
        turn_id="turn-1",
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert claim is not None
    assert claim.document_ids == [doc1, doc2]
    assert claim.query == "总结附件"
    assert db.context_entries == 1
    claim_sql, params = db.calls[0]
    assert "processing_status in ('waiting_files', 'answer_failed')" in claim_sql
    assert "set processing_status = 'answering'" in claim_sql
    assert "exists" in claim_sql
    assert "not exists" in claim_sql
    assert "d.status = 'indexed'" in claim_sql
    assert "ma.ignored = false" in claim_sql
    assert params["user_id"] == "user-1"
    scope_sql, _ = db.calls[1]
    assert "select distinct ma.document_id" in scope_sql
    assert "d.status = 'indexed'" in scope_sql


@pytest.mark.asyncio
async def test_claim_ready_answer_returns_none_when_another_request_claimed(
    monkeypatch,
) -> None:
    db = _FakeDB([_Result(row=None)])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    claim = await ChatTurnRepository().claim_ready_answer(
        turn_id="turn-1",
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert claim is None
    assert len(db.calls) == 1


@pytest.mark.asyncio
async def test_claim_ready_answer_rejects_empty_scope_after_claim(monkeypatch) -> None:
    db = _FakeDB([
        _Result(row=_row(
            message_id="turn-1",
            session_id="session-1",
            space_id="33333333-3333-4333-8333-333333333333",
            content="总结附件",
        )),
        _Result(rows=[]),
    ])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    with pytest.raises(RuntimeError, match="文档范围"):
        await ChatTurnRepository().claim_ready_answer(
            turn_id="turn-1",
            tenant_id="tenant-1",
            user_id="user-1",
        )


@pytest.mark.asyncio
async def test_cancel_rejects_answering_or_completed_turn(monkeypatch) -> None:
    db = _FakeDB([_Result(row=None), _Result(row=None)])
    _patch_session(monkeypatch, repo_module, "get_session", db)
    repository = ChatTurnRepository()

    answering = await repository.cancel_turn(
        turn_id="turn-1", tenant_id="tenant-1", user_id="user-1"
    )
    completed = await repository.cancel_turn(
        turn_id="turn-2", tenant_id="tenant-1", user_id="user-1"
    )

    assert answering is False
    assert completed is False
    sql, _ = db.calls[0]
    assert "processing_status in ('waiting_files', 'answer_failed')" in sql
    assert "processing_status = 'cancelled'" in sql


@pytest.mark.asyncio
async def test_fail_answer_records_error_only_while_answering(monkeypatch) -> None:
    db = _FakeDB([_Result(row=_row(message_id="turn-1"))])
    _patch_session(monkeypatch, repo_module, "get_session", db)

    changed = await ChatTurnRepository().fail_answer(
        turn_id="turn-1",
        tenant_id="tenant-1",
        user_id="user-1",
        error_message="模型服务超时",
    )

    assert changed is True
    sql, params = db.calls[0]
    assert "processing_status = 'answer_failed'" in sql
    assert "m.processing_status = 'answering'" in sql
    assert "s.user_id = :user_id" in sql
    assert params["error_message"] == "模型服务超时"


@pytest.mark.asyncio
async def test_session_message_queries_require_tenant_and_user(monkeypatch) -> None:
    created_at = datetime(2026, 8, 28, tzinfo=UTC)
    message = _row(
        message_id="message-1",
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        role="user",
        content="附件问题",
        token_count=3,
        processing_status="waiting_files",
        processing_error=None,
        client_request_id="request-1",
        created_at=created_at,
    )
    db = _FakeDB([_Result(rows=[message]) for _ in range(4)])
    _patch_session(monkeypatch, session_repo_module, "get_db", db)
    repository = SessionRepository()

    all_messages = await repository.get_all_messages("session-1", "tenant-1", "user-1")
    await repository.get_first_messages("session-1", "tenant-1", "user-1", 2)
    await repository.get_last_messages("session-1", "tenant-1", "user-1", 2)
    await repository.get_middle_messages("session-1", "tenant-1", "user-1", 1, 0)

    assert all_messages[0].processing_status == "waiting_files"
    assert all_messages[0].client_request_id == "request-1"
    for sql, params in db.calls:
        assert "user_id = :user_id" in sql
        assert "processing_status" in sql
        assert "processing_error" in sql
        assert "client_request_id" in sql
        assert params["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_session_lookup_returns_trusted_space_id(monkeypatch) -> None:
    created_at = datetime(2026, 8, 28, tzinfo=UTC)
    db = _FakeDB([_Result(row=_row(
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        space_id="space-1",
        title=None,
        summary=None,
        message_count=0,
        created_at=created_at,
        updated_at=created_at,
    ))])
    _patch_session(monkeypatch, session_repo_module, "get_db", db)

    session = await SessionRepository().get_by_id(
        "session-1", "tenant-1", "user-1"
    )

    assert session is not None
    assert session.space_id == "space-1"
    sql, _ = db.calls[0]
    assert "space_id" in sql
