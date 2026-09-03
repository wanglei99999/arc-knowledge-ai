from __future__ import annotations

from app.domain.chat_turn import (
    AttachmentStatus,
    AttachmentView,
    TurnReadiness,
    compute_readiness,
)
from app.domain.memory import Message


def _attachment(
    status: AttachmentStatus,
    *,
    ignored: bool = False,
) -> AttachmentView:
    return AttachmentView(
        attachment_id="attachment-1",
        client_id="client-1",
        file_name="example.pdf",
        mime_type="application/pdf",
        file_size=1024,
        document_id=None,
        status=status,
        ignored=ignored,
        error_message=None,
    )


def test_readiness_is_ingesting_while_an_attachment_is_pending() -> None:
    readiness = compute_readiness(
        [
            _attachment(AttachmentStatus.INDEXED),
            _attachment(AttachmentStatus.PENDING_UPLOAD),
        ]
    )

    assert readiness is TurnReadiness.INGESTING


def test_readiness_is_blocked_for_an_active_failure() -> None:
    readiness = compute_readiness(
        [
            _attachment(AttachmentStatus.FAILED),
        ]
    )

    assert readiness is TurnReadiness.BLOCKED


def test_readiness_prioritizes_failure_over_ingestion() -> None:
    readiness = compute_readiness(
        [
            _attachment(AttachmentStatus.FAILED),
            _attachment(AttachmentStatus.INGESTING),
        ]
    )

    assert readiness is TurnReadiness.BLOCKED


def test_readiness_is_ready_when_every_active_attachment_is_indexed() -> None:
    readiness = compute_readiness(
        [
            _attachment(AttachmentStatus.INDEXED),
            _attachment(AttachmentStatus.FAILED, ignored=True),
        ]
    )

    assert readiness is TurnReadiness.READY


def test_readiness_is_empty_when_every_attachment_is_ignored() -> None:
    readiness = compute_readiness(
        [
            _attachment(AttachmentStatus.FAILED, ignored=True),
            _attachment(AttachmentStatus.IGNORED),
        ]
    )

    assert readiness is TurnReadiness.EMPTY


def test_readiness_is_empty_without_attachments() -> None:
    assert compute_readiness([]) is TurnReadiness.EMPTY


def test_message_processing_fields_default_to_none() -> None:
    message = Message(
        message_id="message-1",
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        role="user",
        content="普通历史消息",
    )

    assert message.processing_status is None
    assert message.processing_error is None
    assert message.client_request_id is None
