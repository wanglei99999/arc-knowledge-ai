from __future__ import annotations

from dataclasses import dataclass, replace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import UserContext, require_user
from app.api.routers import chat_turn as chat_turn_router
from app.api.routers import session as session_router
from app.domain.chat_turn import (
    AnswerClaim,
    AssistantAnswerView,
    AttachmentStatus,
    AttachmentView,
    ChatTurnView,
    TurnProcessingStatus,
    TurnReadiness,
)
from app.services.chat_turn_service import AttachmentUploadResult
from app.services.session_service import SessionMessageView


class _InMemoryChatBackend:
    def __init__(self) -> None:
        self.turn: ChatTurnView | None = None
        self.messages: list[SessionMessageView] = []
        self.scoped_document_ids: list[list[str]] = []

    async def create_turn(
        self,
        *,
        client_request_id,
        session_id,
        tenant_id,
        user_id,
        query,
        attachments,
    ) -> ChatTurnView:
        declaration = attachments[0]
        attachment = AttachmentView(
            attachment_id="attachment-1",
            client_id=declaration.client_id,
            file_name=declaration.file_name,
            mime_type=declaration.mime_type,
            file_size=declaration.file_size,
            document_id=None,
            status=AttachmentStatus.PENDING_UPLOAD,
            ignored=False,
            error_message=None,
        )
        self.turn = ChatTurnView(
            turn_id="turn-1",
            session_id=session_id,
            space_id="space-1",
            query=query,
            readiness=TurnReadiness.INGESTING,
            processing_status=TurnProcessingStatus.WAITING_FILES,
            processing_error=None,
            attachments=[attachment],
            assistant=None,
        )
        self.messages = [self._user_history()]
        return self.turn

    async def upload_attachment(self, **kwargs) -> AttachmentUploadResult:
        turn = self._require_turn()
        attachment = replace(
            turn.attachments[0],
            document_id="document-1",
            status=AttachmentStatus.INGESTING,
        )
        self.turn = replace(turn, attachments=[attachment])
        self.messages[0] = self._user_history()
        return AttachmentUploadResult("document-1", "task-1", "run-1")

    async def get_turn(self, **kwargs) -> ChatTurnView:
        return self._require_turn()

    def mark_indexed(self) -> None:
        turn = self._require_turn()
        attachment = replace(
            turn.attachments[0], status=AttachmentStatus.INDEXED
        )
        self.turn = replace(
            turn,
            readiness=TurnReadiness.READY,
            attachments=[attachment],
        )
        self.messages[0] = self._user_history()

    async def prepare_answer(self, **kwargs) -> AnswerClaim:
        turn = self._require_turn()
        assert turn.readiness is TurnReadiness.READY
        self.turn = replace(
            turn, processing_status=TurnProcessingStatus.ANSWERING
        )
        self.messages[0] = self._user_history()
        return AnswerClaim(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            tenant_id=kwargs["tenant_id"],
            user_id=kwargs["user_id"],
            space_id=turn.space_id,
            query=turn.query,
            document_ids=["document-1"],
        )

    async def stream_answer(self, claim):
        self.scoped_document_ids.append(list(claim.document_ids))
        yield "金额为 100 万元。"
        citations = [{
            "doc_id": "document-1",
            "chunk_id": "chunk-1",
            "doc_name": "contract.pdf",
            "content": "合同金额为 100 万元",
            "score": 0.98,
            "source": "hybrid",
            "rank": 1,
        }]
        turn = self._require_turn()
        assistant = AssistantAnswerView(
            message_id="answer-1",
            content="金额为 100 万元。",
            citations=citations,
        )
        self.turn = replace(
            turn,
            processing_status=TurnProcessingStatus.COMPLETED,
            assistant=assistant,
        )
        self.messages[0] = self._user_history()
        self.messages.append(SessionMessageView(
            message_id="answer-1",
            role="assistant",
            content=assistant.content,
            processing_status=None,
            processing_error=None,
            attachments=[],
            citations=citations,
        ))
        yield citations

    async def get_messages(self, session_id, tenant_id, user_id, limit):
        return self.messages[-limit:]

    def _user_history(self) -> SessionMessageView:
        turn = self._require_turn()
        return SessionMessageView(
            message_id=turn.turn_id,
            role="user",
            content=turn.query,
            processing_status=turn.processing_status.value,
            processing_error=turn.processing_error,
            attachments=turn.attachments,
            citations=[],
        )

    def _require_turn(self) -> ChatTurnView:
        if self.turn is None:
            raise AssertionError("turn has not been created")
        return self.turn


@dataclass(frozen=True)
class ChatTurnTestApp:
    client: AsyncClient
    backend: _InMemoryChatBackend


@pytest.fixture
async def chat_turn_test_app(monkeypatch):
    backend = _InMemoryChatBackend()
    monkeypatch.setattr(chat_turn_router, "_service", backend)
    monkeypatch.setattr(session_router, "_service", backend)

    app = FastAPI()
    app.include_router(chat_turn_router.router)
    app.include_router(session_router.router)
    app.dependency_overrides[require_user] = lambda: UserContext(
        tenant_id="tenant-1", user_id="user-1"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield ChatTurnTestApp(client=client, backend=backend)
