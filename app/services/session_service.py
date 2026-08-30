from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.domain.chat_turn import AttachmentView
from app.domain.memory import Session
from app.infrastructure.postgres.repositories.chat_turn_repo import ChatTurnRepository
from app.infrastructure.postgres.repositories.citation_repo import CitationRepository
from app.infrastructure.postgres.repositories.memory_repo import MemoryRepository
from app.infrastructure.postgres.repositories.session_repo import SessionRepository


@dataclass
class SessionListResult:
    sessions: list[Session]
    total: int


@dataclass(frozen=True)
class SessionMessageView:
    message_id: str
    role: str
    content: str
    processing_status: str | None
    processing_error: str | None
    attachments: list[AttachmentView]
    citations: list[dict]


class SessionService:
    """会话管理业务逻辑"""

    def __init__(
        self,
        *,
        session_repo: SessionRepository | None = None,
        memory_repo: MemoryRepository | None = None,
        citation_repo: CitationRepository | None = None,
        attachment_repo: ChatTurnRepository | None = None,
    ) -> None:
        self._session_repo = session_repo or SessionRepository()
        self._memory_repo = memory_repo or MemoryRepository()
        self._citation_repo = citation_repo or CitationRepository()
        self._attachment_repo = attachment_repo or ChatTurnRepository()

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        title: str | None = None,
        space_id: str | None = None,
    ) -> Session:
        return await self._session_repo.create(tenant_id, user_id, title, space_id)

    async def get(
        self, session_id: str, tenant_id: str, user_id: str
    ) -> Session | None:
        return await self._session_repo.get_by_id(session_id, tenant_id, user_id)

    async def list(
        self, tenant_id: str, user_id: str, limit: int = 20, offset: int = 0,
        space_id: str | None = None,
    ) -> SessionListResult:
        sessions = await self._session_repo.list(tenant_id, user_id, limit, offset, space_id)
        return SessionListResult(sessions=sessions, total=len(sessions))

    async def delete(
        self, session_id: str, tenant_id: str, user_id: str
    ) -> bool:
        """删除会话及其关联记忆"""
        deleted = await self._session_repo.delete(session_id, tenant_id, user_id)
        if deleted:
            await self._memory_repo.delete_by_session(session_id)
        return deleted

    async def get_messages(
        self, session_id: str, tenant_id: str, user_id: str, limit: int = 50
    ) -> list[SessionMessageView]:
        messages = await self._session_repo.get_last_messages(
            session_id, tenant_id, user_id, n=limit
        )
        message_ids = [message.message_id for message in messages]
        assistant_ids = [
            message.message_id for message in messages if message.role == "assistant"
        ]
        citations_map, attachments_map = await asyncio.gather(
            self._citation_repo.get_by_message_ids(assistant_ids, tenant_id),
            self._attachment_repo.get_by_message_ids(message_ids, tenant_id),
        )
        return [
            SessionMessageView(
                message_id=message.message_id,
                role=message.role,
                content=message.content,
                processing_status=message.processing_status,
                processing_error=message.processing_error,
                attachments=attachments_map.get(message.message_id, []),
                citations=(
                    citations_map.get(message.message_id, [])
                    if message.role == "assistant"
                    else []
                ),
            )
            for message in messages
        ]
