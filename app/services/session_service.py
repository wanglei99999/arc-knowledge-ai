from __future__ import annotations

from dataclasses import dataclass

from app.domain.memory import Message, Session
from app.infrastructure.postgres.repositories.memory_repo import MemoryRepository
from app.infrastructure.postgres.repositories.session_repo import SessionRepository


@dataclass
class SessionListResult:
    sessions: list[Session]
    total: int


class SessionService:
    """会话管理业务逻辑"""

    def __init__(self) -> None:
        self._session_repo = SessionRepository()
        self._memory_repo  = MemoryRepository()

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
        self, tenant_id: str, user_id: str, limit: int = 20, offset: int = 0
    ) -> SessionListResult:
        sessions = await self._session_repo.list(tenant_id, user_id, limit, offset)
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
    ) -> list[Message]:
        return await self._session_repo.get_last_messages(
            session_id, tenant_id, user_id, n=limit
        )
