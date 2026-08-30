from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import UserContext, require_user
from app.api.routers import session as session_router
from app.domain.chat_turn import AttachmentStatus, AttachmentView
from app.domain.memory import Message
from app.services.session_service import SessionService


class _HistoryService:
    async def get_messages(self, session_id, tenant_id, user_id, limit):
        return [
            SimpleNamespace(
                message_id="message-1",
                role="user",
                content="普通问题",
                processing_status=None,
                processing_error=None,
                attachments=[],
                citations=[],
            ),
            SimpleNamespace(
                message_id="message-2",
                role="assistant",
                content="普通回答",
                processing_status=None,
                processing_error=None,
                attachments=[],
                citations=[{"doc_id": "document-ordinary", "rank": 1}],
            ),
            SimpleNamespace(
                message_id="turn-1",
                role="user",
                content="请回答附件",
                processing_status="completed",
                processing_error=None,
                attachments=[
                    AttachmentView(
                        attachment_id="attachment-1",
                        client_id="client-1",
                        file_name="contract.pdf",
                        mime_type="application/pdf",
                        file_size=1024,
                        document_id="document-1",
                        status=AttachmentStatus.INDEXED,
                        ignored=False,
                        error_message=None,
                    )
                ],
                citations=[],
            ),
        ]


class _ForbiddenCitationRepository:
    async def get_by_message_ids(self, *args, **kwargs):
        raise AssertionError("citations must be hydrated by SessionService")


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(session_router, "_service", _HistoryService())
    monkeypatch.setattr(
        session_router,
        "_citation_repo",
        _ForbiddenCitationRepository(),
        raising=False,
    )
    app = FastAPI()
    app.include_router(session_router.router)
    app.dependency_overrides[require_user] = lambda: UserContext(
        tenant_id="tenant-1", user_id="user-1"
    )
    return TestClient(app, raise_server_exceptions=False)


def test_session_history_hydrates_attachments_and_keeps_citations_on_assistant(
    client: TestClient,
) -> None:
    response = client.get("/sessions/session-1/messages")

    assert response.status_code == 200
    assert response.json() == [
        {
            "message_id": "message-1",
            "role": "user",
            "content": "普通问题",
            "processing_status": None,
            "processing_error": None,
            "attachments": [],
            "citations": [],
        },
        {
            "message_id": "message-2",
            "role": "assistant",
            "content": "普通回答",
            "processing_status": None,
            "processing_error": None,
            "attachments": [],
            "citations": [{"doc_id": "document-ordinary", "rank": 1}],
        },
        {
            "message_id": "turn-1",
            "role": "user",
            "content": "请回答附件",
            "processing_status": "completed",
            "processing_error": None,
            "attachments": [
                {
                    "attachment_id": "attachment-1",
                    "client_id": "client-1",
                    "file_name": "contract.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 1024,
                    "document_id": "document-1",
                    "status": "indexed",
                    "ignored": False,
                    "error_message": None,
                }
            ],
            "citations": [],
        },
    ]


class _SessionRepository:
    def __init__(self, messages: list[Message]) -> None:
        self.messages = messages
        self.calls: list[tuple[str, str, str, int]] = []

    async def get_last_messages(self, session_id, tenant_id, user_id, n):
        self.calls.append((session_id, tenant_id, user_id, n))
        return self.messages


class _CitationRepository:
    def __init__(self, values: dict[str, list[dict]]) -> None:
        self.values = values
        self.calls: list[tuple[list[str], str]] = []

    async def get_by_message_ids(self, message_ids, tenant_id):
        self.calls.append((message_ids, tenant_id))
        return self.values


class _AttachmentRepository:
    def __init__(self, values: dict[str, list[AttachmentView]]) -> None:
        self.values = values
        self.calls: list[tuple[list[str], str]] = []

    async def get_by_message_ids(self, message_ids, tenant_id):
        self.calls.append((message_ids, tenant_id))
        return self.values


class _MemoryRepository:
    pass


@pytest.mark.asyncio
async def test_session_service_batch_hydrates_messages_without_n_plus_one() -> None:
    messages = [
        Message(
            message_id="turn-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            role="user",
            content="请回答附件",
            processing_status="completed",
        ),
        Message(
            message_id="answer-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            role="assistant",
            content="附件回答",
        ),
    ]
    attachment = AttachmentView(
        attachment_id="attachment-1",
        client_id="client-1",
        file_name="contract.pdf",
        mime_type="application/pdf",
        file_size=1024,
        document_id="document-1",
        status=AttachmentStatus.INDEXED,
        ignored=False,
        error_message=None,
    )
    session_repo = _SessionRepository(messages)
    citation_repo = _CitationRepository({
        "answer-1": [{"doc_id": "document-1", "rank": 1}],
        "turn-1": [{"doc_id": "must-not-leak-to-user", "rank": 1}],
    })
    attachment_repo = _AttachmentRepository({"turn-1": [attachment]})
    service = SessionService(
        session_repo=session_repo,
        memory_repo=_MemoryRepository(),
        citation_repo=citation_repo,
        attachment_repo=attachment_repo,
    )

    result = await service.get_messages(
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        limit=50,
    )

    assert result[0].processing_status == "completed"
    assert result[0].attachments == [attachment]
    assert result[0].citations == []
    assert result[1].attachments == []
    assert result[1].citations == [{"doc_id": "document-1", "rank": 1}]
    assert session_repo.calls == [("session-1", "tenant-1", "user-1", 50)]
    assert citation_repo.calls == [(["answer-1"], "tenant-1")]
    assert attachment_repo.calls == [(["turn-1", "answer-1"], "tenant-1")]
