from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.chat_turn import (
    AttachmentDeclaration,
    AttachmentStatus,
    AttachmentView,
    ChatTurnView,
    TurnProcessingStatus,
    TurnReadiness,
)
from app.services.chat_turn_service import (
    ChatTurnConflictError,
    ChatTurnNotFoundError,
    ChatTurnService,
    ChatTurnValidationError,
)
from app.services.document_service import IngestResult


def _attachment(**overrides) -> AttachmentView:
    values = {
        "attachment_id": "attachment-1",
        "client_id": "client-1",
        "file_name": "contract.pdf",
        "mime_type": "application/pdf",
        "file_size": 8,
        "document_id": None,
        "status": AttachmentStatus.PENDING_UPLOAD,
        "ignored": False,
        "error_message": None,
    }
    values.update(overrides)
    return AttachmentView(**values)


def _turn(**overrides) -> ChatTurnView:
    values = {
        "turn_id": "turn-1",
        "session_id": "session-1",
        "space_id": "space-1",
        "query": "请总结附件",
        "readiness": TurnReadiness.INGESTING,
        "processing_status": TurnProcessingStatus.WAITING_FILES,
        "processing_error": None,
        "attachments": [_attachment()],
        "assistant": None,
    }
    values.update(overrides)
    return ChatTurnView(**values)


class _TurnRepo:
    def __init__(self, turn: ChatTurnView | None = None) -> None:
        self.turn = turn
        self.created: list[dict] = []
        self.linked: list[dict] = []
        self.failed: list[dict] = []
        self.ignored: list[dict] = []
        self.added: list[dict] = []
        self.cancelled: list[dict] = []

    async def create_turn(self, **kwargs):
        self.created.append(kwargs)
        return self.turn or _turn()

    async def get_turn(self, **kwargs):
        return self.turn

    async def link_document(self, **kwargs):
        self.linked.append(kwargs)
        if self.turn is not None:
            linked = replace(
                self.turn.attachments[0],
                document_id=kwargs["document_id"],
                status=AttachmentStatus.INGESTING,
            )
            self.turn = replace(self.turn, attachments=[linked])
        return True

    async def mark_upload_failed(self, **kwargs):
        self.failed.append(kwargs)
        return True

    async def set_ignored(self, **kwargs):
        self.ignored.append(kwargs)
        if self.turn is not None:
            item = replace(
                self.turn.attachments[0],
                ignored=kwargs["ignored"],
                status=(AttachmentStatus.IGNORED if kwargs["ignored"] else AttachmentStatus.FAILED),
            )
            self.turn = replace(
                self.turn,
                attachments=[item],
                readiness=(TurnReadiness.EMPTY if kwargs["ignored"] else TurnReadiness.BLOCKED),
            )
        return True

    async def add_attachment(self, **kwargs):
        self.added.append(kwargs)
        if self.turn is not None:
            declaration = kwargs["attachment"]
            item = _attachment(
                attachment_id="attachment-2",
                client_id=declaration.client_id,
                file_name=declaration.file_name,
                mime_type=declaration.mime_type,
                file_size=declaration.file_size,
            )
            self.turn = replace(
                self.turn,
                attachments=[*self.turn.attachments, item],
                readiness=TurnReadiness.INGESTING,
            )
        return True

    async def cancel_turn(self, **kwargs):
        self.cancelled.append(kwargs)
        if self.turn is not None:
            self.turn = replace(
                self.turn,
                processing_status=TurnProcessingStatus.CANCELLED,
            )
        return True


class _SessionRepo:
    def __init__(self, session=None) -> None:
        self.session = session

    async def get_by_id(self, session_id, tenant_id, user_id):
        return self.session


class _ChunkRepo:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create_document(self, **kwargs):
        self.created.append(kwargs)


class _DocumentService:
    def __init__(self) -> None:
        self.ingested = []
        self.retried = []

    async def ingest(self, req):
        self.ingested.append(req)
        return IngestResult(req.document_id, "task-1", "run-1")

    async def retry_ingestion(self, document_id, tenant_id):
        self.retried.append((document_id, tenant_id))
        return IngestResult(document_id, "task-2", "run-2")


def _service(
    *,
    turn: ChatTurnView | None = None,
    session=None,
    upload=None,
) -> tuple[ChatTurnService, _TurnRepo, _ChunkRepo, _DocumentService]:
    turn_repo = _TurnRepo(turn)
    chunk_repo = _ChunkRepo()
    document_service = _DocumentService()

    async def default_upload(*args, **kwargs):
        return None

    service = ChatTurnService(
        turn_repo=turn_repo,
        session_repo=_SessionRepo(session),
        chunk_repo=chunk_repo,
        document_service=document_service,
        upload_file_fn=upload or default_upload,
        object_key_builder=lambda *args: "tenant/space/document.pdf",
        max_file_size_bytes=100,
    )
    return service, turn_repo, chunk_repo, document_service


@pytest.mark.asyncio
async def test_create_rejects_more_than_five_attachments() -> None:
    service, *_ = _service()
    attachments = [
        AttachmentDeclaration(str(i), f"{i}.pdf", "application/pdf", 9) for i in range(6)
    ]

    with pytest.raises(ChatTurnValidationError, match="5"):
        await service.create_turn(
            client_request_id="request-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            query="总结",
            attachments=attachments,
        )


@pytest.mark.asyncio
async def test_create_rejects_blank_question() -> None:
    service, *_ = _service()

    with pytest.raises(ChatTurnValidationError, match="问题"):
        await service.create_turn(
            client_request_id="request-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            query="  ",
            attachments=[AttachmentDeclaration("client-1", "a.pdf", "application/pdf", 9)],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "declaration",
    [
        AttachmentDeclaration("c", "bad.exe", "application/x-msdownload", 9),
        AttachmentDeclaration("c", "huge.pdf", "application/pdf", 101),
        AttachmentDeclaration("c", "empty.pdf", "application/pdf", 0),
    ],
)
async def test_create_rejects_disallowed_mime_or_declared_size(declaration) -> None:
    service, *_ = _service()

    with pytest.raises(ChatTurnValidationError):
        await service.create_turn(
            client_request_id="request-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            query="总结",
            attachments=[declaration],
        )


@pytest.mark.asyncio
async def test_create_reads_trusted_space_from_owned_session() -> None:
    from app.domain.memory import Session

    session = Session("session-1", "tenant-1", "user-1", space_id="space-1")
    service, turn_repo, *_ = _service(turn=_turn(), session=session)

    await service.create_turn(
        client_request_id="request-1",
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        query=" 总结 ",
        attachments=[AttachmentDeclaration("client-1", "contract.pdf", "application/pdf", 9)],
    )

    assert turn_repo.created[0]["space_id"] == "space-1"
    assert turn_repo.created[0]["query"] == "总结"


@pytest.mark.asyncio
async def test_upload_rejects_attachment_from_other_user() -> None:
    service, *_ = _service(turn=None)

    with pytest.raises(ChatTurnNotFoundError):
        await service.upload_attachment(
            turn_id="other-turn",
            attachment_id="attachment-1",
            tenant_id="tenant-1",
            user_id="user-1",
            file_name="contract.pdf",
            mime_type="application/pdf",
            data=b"%PDF-1.7",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_name,mime_type,data",
    [
        ("other.pdf", "application/pdf", b"%PDF-1.7"),
        ("contract.pdf", "text/plain", b"%PDF-1.7"),
        ("contract.pdf", "application/pdf", b"not-pdf!"),
    ],
)
async def test_upload_rejects_actual_name_mime_or_size_mismatch(file_name, mime_type, data) -> None:
    service, *_ = _service(turn=_turn())

    with pytest.raises(ChatTurnValidationError):
        await service.upload_attachment(
            turn_id="turn-1",
            attachment_id="attachment-1",
            tenant_id="tenant-1",
            user_id="user-1",
            file_name=file_name,
            mime_type=mime_type,
            data=data,
        )


@pytest.mark.asyncio
async def test_upload_reuses_existing_document_link() -> None:
    linked = _attachment(
        document_id="document-1",
        status=AttachmentStatus.INGESTING,
    )
    service, turn_repo, chunk_repo, document_service = _service(turn=_turn(attachments=[linked]))

    result = await service.upload_attachment(
        turn_id="turn-1",
        attachment_id="attachment-1",
        tenant_id="tenant-1",
        user_id="user-1",
        file_name="contract.pdf",
        mime_type="application/pdf",
        data=b"%PDF-1.7",
    )

    assert result.document_id == "document-1"
    assert result.task_id is None
    assert not turn_repo.linked
    assert not chunk_repo.created
    assert not document_service.ingested


@pytest.mark.asyncio
async def test_upload_records_safe_failure_message() -> None:
    async def fail_upload(*args, **kwargs):
        raise RuntimeError("secret endpoint and credentials")

    service, turn_repo, *_ = _service(turn=_turn(), upload=fail_upload)

    with pytest.raises(ChatTurnConflictError, match="上传失败"):
        await service.upload_attachment(
            turn_id="turn-1",
            attachment_id="attachment-1",
            tenant_id="tenant-1",
            user_id="user-1",
            file_name="contract.pdf",
            mime_type="application/pdf",
            data=b"%PDF-1.7",
        )

    assert turn_repo.failed[0]["error_message"] == "文件上传失败，请重试"


@pytest.mark.asyncio
async def test_ignore_changes_readiness_without_deleting_document() -> None:
    failed = _attachment(
        document_id="document-1",
        status=AttachmentStatus.FAILED,
        error_message="解析失败",
    )
    service, turn_repo, _, document_service = _service(
        turn=_turn(attachments=[failed], readiness=TurnReadiness.BLOCKED)
    )

    turn = await service.set_ignored(
        turn_id="turn-1",
        attachment_id="attachment-1",
        tenant_id="tenant-1",
        user_id="user-1",
        ignored=True,
    )

    assert turn.readiness is TurnReadiness.EMPTY
    assert turn_repo.ignored[0]["ignored"] is True
    assert not document_service.retried


@pytest.mark.asyncio
async def test_add_attachment_reopens_empty_turn() -> None:
    ignored = _attachment(status=AttachmentStatus.IGNORED, ignored=True)
    service, turn_repo, *_ = _service(
        turn=_turn(attachments=[ignored], readiness=TurnReadiness.EMPTY)
    )
    declaration = AttachmentDeclaration("client-2", "appendix.pdf", "application/pdf", 9)

    turn = await service.add_attachment(
        turn_id="turn-1",
        tenant_id="tenant-1",
        user_id="user-1",
        attachment=declaration,
    )

    assert turn.readiness is TurnReadiness.INGESTING
    assert turn_repo.added[0]["max_attachments"] == 5


@pytest.mark.asyncio
async def test_cancel_unlocks_waiting_turn() -> None:
    service, turn_repo, *_ = _service(turn=_turn())

    turn = await service.cancel_turn(turn_id="turn-1", tenant_id="tenant-1", user_id="user-1")

    assert turn.processing_status is TurnProcessingStatus.CANCELLED
    assert turn_repo.cancelled


@pytest.mark.asyncio
async def test_retry_requires_failed_linked_attachment() -> None:
    service, *_ = _service(turn=_turn())

    with pytest.raises(ChatTurnConflictError):
        await service.retry_attachment(
            turn_id="turn-1",
            attachment_id="attachment-1",
            tenant_id="tenant-1",
            user_id="user-1",
        )


@pytest.mark.asyncio
async def test_retry_delegates_failed_document_cleanup() -> None:
    failed = _attachment(
        document_id="document-1",
        status=AttachmentStatus.FAILED,
        error_message="索引失败",
    )
    service, _, _, document_service = _service(
        turn=_turn(attachments=[failed], readiness=TurnReadiness.BLOCKED)
    )

    result = await service.retry_attachment(
        turn_id="turn-1",
        attachment_id="attachment-1",
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert result.document_id == "document-1"
    assert document_service.retried == [("document-1", "tenant-1")]
