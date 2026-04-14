from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.dependencies import UserContext, require_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["memory"])
_service = SessionService()


class CreateSessionBody(BaseModel):
    title: str | None = None


class SessionOut(BaseModel):
    session_id: str
    title: str | None
    summary: str | None
    message_count: int


class MessageOut(BaseModel):
    message_id: str
    role: str
    content: str


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionOut)
async def create_session(
    body: CreateSessionBody,
    user: UserContext = Depends(require_user),
) -> SessionOut:
    session = await _service.create(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        title=body.title,
    )
    return SessionOut(
        session_id=session.session_id,
        title=session.title,
        summary=session.summary,
        message_count=session.message_count,
    )


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    limit: int = 20,
    offset: int = 0,
    user: UserContext = Depends(require_user),
) -> list[SessionOut]:
    result = await _service.list(
        tenant_id=user.tenant_id, user_id=user.user_id,
        limit=limit, offset=offset,
    )
    return [
        SessionOut(
            session_id=s.session_id,
            title=s.title,
            summary=s.summary,
            message_count=s.message_count,
        )
        for s in result.sessions
    ]


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: str,
    user: UserContext = Depends(require_user),
) -> SessionOut:
    session = await _service.get(session_id, user.tenant_id, user.user_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return SessionOut(
        session_id=session.session_id,
        title=session.title,
        summary=session.summary,
        message_count=session.message_count,
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: UserContext = Depends(require_user),
) -> None:
    deleted = await _service.delete(session_id, user.tenant_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_messages(
    session_id: str,
    limit: int = 50,
    user: UserContext = Depends(require_user),
) -> list[MessageOut]:
    messages = await _service.get_messages(session_id, user.tenant_id, user.user_id, limit)
    return [
        MessageOut(message_id=m.message_id, role=m.role, content=m.content)
        for m in messages
    ]
