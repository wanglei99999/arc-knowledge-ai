from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import UserContext, require_user
from app.api.routers.chat_turn import AttachmentOut
from app.services.session_service import (
    SessionBusyError,
    SessionService,
    SessionSpaceArchivedError,
)

router = APIRouter(prefix="/sessions", tags=["memory"])
_service = SessionService()


class CreateSessionBody(BaseModel):
    title: str | None = None
    space_id: str | None = None


class RenameSessionBody(BaseModel):
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SessionOut(BaseModel):
    session_id: str
    space_id: str | None
    title: str | None
    summary: str | None
    message_count: int
    pinned_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    message_id: str
    role: str
    content: str
    processing_status: str | None = None
    processing_error: str | None = None
    attachments: list[AttachmentOut] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)


class SpaceSummaryOut(BaseModel):
    space_id: str
    name: str
    status: str


class ArchivedSessionOut(BaseModel):
    session_id: str
    title: str | None
    message_count: int
    archived_at: datetime
    space: SpaceSummaryOut


class ArchivedSessionPageOut(BaseModel):
    items: list[ArchivedSessionOut]
    total: int


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionOut)
async def create_session(
    body: CreateSessionBody,
    user: UserContext = Depends(require_user),
) -> SessionOut:
    session = await _service.create(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        title=body.title,
        space_id=body.space_id,
    )
    return SessionOut(
        session_id=session.session_id,
        space_id=session.space_id,
        title=session.title,
        summary=session.summary,
        message_count=session.message_count,
        pinned_at=session.pinned_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    limit: int = 20,
    offset: int = 0,
    space_id: str | None = None,
    user: UserContext = Depends(require_user),
) -> list[SessionOut]:
    result = await _service.list(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        limit=limit,
        offset=offset,
        space_id=space_id,
    )
    return [
        SessionOut(
            session_id=s.session_id,
            space_id=s.space_id,
            title=s.title,
            summary=s.summary,
            message_count=s.message_count,
            pinned_at=s.pinned_at,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in result.sessions
    ]


@router.get("/archived", response_model=ArchivedSessionPageOut)
async def list_archived_sessions(
    query: str | None = None,
    space_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserContext = Depends(require_user),
) -> ArchivedSessionPageOut:
    page = await _service.list_archived(
        user.tenant_id,
        user.user_id,
        query=query,
        space_id=space_id,
        limit=limit,
        offset=offset,
    )
    return ArchivedSessionPageOut(
        items=[
            ArchivedSessionOut(
                session_id=item.session_id,
                title=item.title,
                message_count=item.message_count,
                archived_at=item.archived_at,
                space=SpaceSummaryOut(
                    space_id=item.space_id,
                    name=item.space_name,
                    status=item.space_status,
                ),
            )
            for item in page.items
        ],
        total=page.total,
    )


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
        space_id=session.space_id,
        title=session.title,
        summary=session.summary,
        message_count=session.message_count,
        pinned_at=session.pinned_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.patch("/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: str,
    body: RenameSessionBody,
    user: UserContext = Depends(require_user),
) -> SessionOut:
    renamed = await _service.rename(session_id, user.tenant_id, user.user_id, body.title)
    if not renamed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return SessionOut(
        session_id=renamed.session_id,
        space_id=renamed.space_id,
        title=renamed.title,
        summary=renamed.summary,
        message_count=renamed.message_count,
        pinned_at=renamed.pinned_at,
        created_at=renamed.created_at,
        updated_at=renamed.updated_at,
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: UserContext = Depends(require_user),
) -> None:
    deleted = await _service.delete(session_id, user.tenant_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.post("/{session_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive_session(
    session_id: str,
    user: UserContext = Depends(require_user),
) -> None:
    try:
        archived = await _service.archive(session_id, user.tenant_id, user.user_id)
    except SessionBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SESSION_BUSY",
                "message": "会话正在处理文件或生成回答，暂时无法归档",
            },
        ) from exc
    if not archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )


@router.post("/{session_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_session(
    session_id: str,
    user: UserContext = Depends(require_user),
) -> None:
    try:
        restored = await _service.restore(session_id, user.tenant_id, user.user_id)
    except SessionSpaceArchivedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SPACE_ARCHIVED",
                "message": "请先恢复会话所属的知识空间",
            },
        ) from exc
    if not restored:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )


@router.post("/{session_id}/pin", response_model=SessionOut)
async def pin_session(
    session_id: str,
    user: UserContext = Depends(require_user),
) -> SessionOut:
    pinned = await _service.pin(session_id, user.tenant_id, user.user_id)
    if not pinned:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return SessionOut(
        session_id=pinned.session_id,
        space_id=pinned.space_id,
        title=pinned.title,
        summary=pinned.summary,
        message_count=pinned.message_count,
        pinned_at=pinned.pinned_at,
        created_at=pinned.created_at,
        updated_at=pinned.updated_at,
    )


@router.post("/{session_id}/unpin", response_model=SessionOut)
async def unpin_session(
    session_id: str,
    user: UserContext = Depends(require_user),
) -> SessionOut:
    unpinned = await _service.unpin(session_id, user.tenant_id, user.user_id)
    if not unpinned:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return SessionOut(
        session_id=unpinned.session_id,
        space_id=unpinned.space_id,
        title=unpinned.title,
        summary=unpinned.summary,
        message_count=unpinned.message_count,
        pinned_at=unpinned.pinned_at,
        created_at=unpinned.created_at,
        updated_at=unpinned.updated_at,
    )


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_messages(
    session_id: str,
    limit: int = 50,
    user: UserContext = Depends(require_user),
) -> list[MessageOut]:
    messages = await _service.get_messages(session_id, user.tenant_id, user.user_id, limit)
    return [
        MessageOut(
            message_id=m.message_id,
            role=m.role,
            content=m.content,
            processing_status=m.processing_status,
            processing_error=m.processing_error,
            attachments=[AttachmentOut.from_domain(item) for item in m.attachments],
            citations=m.citations,
        )
        for m in messages
    ]
