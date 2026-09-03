from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.dependencies import UserContext, require_user
from app.domain.chat_turn import (
    AssistantAnswerView,
    AttachmentDeclaration,
    AttachmentView,
    ChatTurnView,
)
from app.services.chat_turn_service import (
    AttachmentUploadResult,
    ChatTurnConflictError,
    ChatTurnError,
    ChatTurnNotFoundError,
    ChatTurnService,
    ChatTurnValidationError,
)
from app.services.document_service import IngestResult

router = APIRouter(prefix="/chat/turns", tags=["chat-turns"])
_service = ChatTurnService()


async def _answer_sse_stream(event_iter: AsyncIterator) -> AsyncIterator[bytes]:
    """将附件回答 token、引用和安全错误包装为现有 SSE 协议。"""
    try:
        async for item in event_iter:
            payload = {"citations": item} if isinstance(item, list) else {"delta": item}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
    except ChatTurnError as exc:
        payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        yield f"data: {payload}\n\n".encode()
    except Exception:
        payload = json.dumps({"error": "回答生成失败，请重试"}, ensure_ascii=False)
        yield f"data: {payload}\n\n".encode()
    finally:
        yield b"data: [DONE]\n\n"


class AttachmentDeclarationBody(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    file_name: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=128)
    file_size: int = Field(gt=0)

    def to_domain(self) -> AttachmentDeclaration:
        return AttachmentDeclaration(
            client_id=self.client_id,
            file_name=self.file_name,
            mime_type=self.mime_type,
            file_size=self.file_size,
        )


class CreateTurnBody(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    query: str
    attachments: list[AttachmentDeclarationBody]


class IgnoreAttachmentBody(BaseModel):
    ignored: bool


class AttachmentOut(BaseModel):
    attachment_id: str
    client_id: str
    file_name: str
    mime_type: str
    file_size: int
    document_id: str | None
    status: str
    ignored: bool
    error_message: str | None

    @classmethod
    def from_domain(cls, item: AttachmentView) -> AttachmentOut:
        return cls(
            attachment_id=item.attachment_id,
            client_id=item.client_id,
            file_name=item.file_name,
            mime_type=item.mime_type,
            file_size=item.file_size,
            document_id=item.document_id,
            status=item.status.value,
            ignored=item.ignored,
            error_message=item.error_message,
        )


class AssistantOut(BaseModel):
    message_id: str
    content: str
    citations: list[dict]

    @classmethod
    def from_domain(cls, answer: AssistantAnswerView) -> AssistantOut:
        return cls(
            message_id=answer.message_id,
            content=answer.content,
            citations=answer.citations,
        )


class ChatTurnOut(BaseModel):
    turn_id: str
    session_id: str
    space_id: str
    query: str
    readiness: str
    processing_status: str
    processing_error: str | None
    attachments: list[AttachmentOut]
    assistant: AssistantOut | None

    @classmethod
    def from_domain(cls, turn: ChatTurnView) -> ChatTurnOut:
        return cls(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            space_id=turn.space_id,
            query=turn.query,
            readiness=turn.readiness.value,
            processing_status=turn.processing_status.value,
            processing_error=turn.processing_error,
            attachments=[AttachmentOut.from_domain(item) for item in turn.attachments],
            assistant=(AssistantOut.from_domain(turn.assistant) if turn.assistant else None),
        )


class IngestOut(BaseModel):
    document_id: str
    task_id: str | None
    workflow_run_id: str | None

    @classmethod
    def from_result(cls, result: AttachmentUploadResult | IngestResult) -> IngestOut:
        return cls(
            document_id=result.document_id,
            task_id=result.task_id,
            workflow_run_id=result.workflow_run_id,
        )


@router.post("", response_model=ChatTurnOut, status_code=status.HTTP_201_CREATED)
async def create_turn(
    body: CreateTurnBody,
    user: UserContext = Depends(require_user),
) -> ChatTurnOut:
    try:
        turn = await _service.create_turn(
            client_request_id=body.client_request_id,
            session_id=body.session_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            query=body.query,
            attachments=[item.to_domain() for item in body.attachments],
        )
    except ChatTurnError as exc:
        _raise_http(exc)
    return ChatTurnOut.from_domain(turn)


@router.put(
    "/{turn_id}/attachments/{attachment_id}",
    response_model=IngestOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_attachment(
    turn_id: str,
    attachment_id: str,
    file: UploadFile,
    user: UserContext = Depends(require_user),
) -> IngestOut:
    try:
        result = await _service.upload_attachment(
            turn_id=turn_id,
            attachment_id=attachment_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            file_name=file.filename or "",
            mime_type=file.content_type or "application/octet-stream",
            data=await file.read(),
        )
    except ChatTurnError as exc:
        _raise_http(exc)
    return IngestOut.from_result(result)


@router.get("/{turn_id}", response_model=ChatTurnOut)
async def get_turn(
    turn_id: str,
    user: UserContext = Depends(require_user),
) -> ChatTurnOut:
    try:
        turn = await _service.get_turn(
            turn_id=turn_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
        )
    except ChatTurnError as exc:
        _raise_http(exc)
    return ChatTurnOut.from_domain(turn)


@router.post("/{turn_id}/answer")
async def answer_turn(
    turn_id: str,
    user: UserContext = Depends(require_user),
) -> StreamingResponse:
    try:
        claim = await _service.prepare_answer(
            turn_id=turn_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
        )
    except ChatTurnError as exc:
        _raise_http(exc)
    return StreamingResponse(
        _answer_sse_stream(_service.stream_answer(claim)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{turn_id}/attachments", response_model=ChatTurnOut)
async def add_attachment(
    turn_id: str,
    body: AttachmentDeclarationBody,
    user: UserContext = Depends(require_user),
) -> ChatTurnOut:
    try:
        turn = await _service.add_attachment(
            turn_id=turn_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            attachment=body.to_domain(),
        )
    except ChatTurnError as exc:
        _raise_http(exc)
    return ChatTurnOut.from_domain(turn)


@router.post(
    "/{turn_id}/attachments/{attachment_id}/retry",
    response_model=IngestOut,
)
async def retry_attachment(
    turn_id: str,
    attachment_id: str,
    user: UserContext = Depends(require_user),
) -> IngestOut:
    try:
        result = await _service.retry_attachment(
            turn_id=turn_id,
            attachment_id=attachment_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
        )
    except ChatTurnError as exc:
        _raise_http(exc)
    return IngestOut.from_result(result)


@router.patch(
    "/{turn_id}/attachments/{attachment_id}",
    response_model=ChatTurnOut,
)
async def set_attachment_ignored(
    turn_id: str,
    attachment_id: str,
    body: IgnoreAttachmentBody,
    user: UserContext = Depends(require_user),
) -> ChatTurnOut:
    try:
        turn = await _service.set_ignored(
            turn_id=turn_id,
            attachment_id=attachment_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            ignored=body.ignored,
        )
    except ChatTurnError as exc:
        _raise_http(exc)
    return ChatTurnOut.from_domain(turn)


@router.post("/{turn_id}/cancel", response_model=ChatTurnOut)
async def cancel_turn(
    turn_id: str,
    user: UserContext = Depends(require_user),
) -> ChatTurnOut:
    try:
        turn = await _service.cancel_turn(
            turn_id=turn_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
        )
    except ChatTurnError as exc:
        _raise_http(exc)
    return ChatTurnOut.from_domain(turn)


def _raise_http(exc: ChatTurnError) -> NoReturn:
    if isinstance(exc, ChatTurnNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, ChatTurnConflictError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, ChatTurnValidationError):
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
    raise HTTPException(status_code=code, detail=str(exc)) from exc
