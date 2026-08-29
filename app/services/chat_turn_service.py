from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config.settings import settings
from app.domain.chat_turn import (
    AttachmentDeclaration,
    AttachmentStatus,
    AttachmentView,
    ChatTurnView,
    TurnProcessingStatus,
    TurnReadiness,
)
from app.infrastructure.minio.client import build_object_key, upload_file
from app.infrastructure.postgres.repositories.chat_turn_repo import ChatTurnRepository
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.infrastructure.postgres.repositories.session_repo import SessionRepository
from app.services.document_service import DocumentService, IngestRequest, IngestResult

MAX_ATTACHMENTS = 5
ALLOWED_MIME_TYPES = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/html",
    "text/markdown",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/bmp",
    "image/webp",
})


class ChatTurnError(Exception):
    """聊天附件业务错误的基类。"""


class ChatTurnValidationError(ChatTurnError):
    """客户端提交的声明或文件内容不合法。"""


class ChatTurnNotFoundError(ChatTurnError):
    """资源不存在，或不属于当前租户和用户。"""


class ChatTurnConflictError(ChatTurnError):
    """当前状态不允许执行该操作。"""


@dataclass(frozen=True)
class AttachmentUploadResult:
    document_id: str
    task_id: str | None
    workflow_run_id: str | None


class ChatTurnService:
    """附件型聊天轮次的校验与跨存储编排。"""

    def __init__(
        self,
        *,
        turn_repo: ChatTurnRepository | None = None,
        session_repo: SessionRepository | None = None,
        chunk_repo: ChunkRepository | None = None,
        document_service: DocumentService | None = None,
        upload_file_fn: Callable[..., Awaitable[str | None]] | None = None,
        object_key_builder: Callable[[str, str, str, str], str] | None = None,
        max_file_size_bytes: int | None = None,
    ) -> None:
        self._turn_repo = turn_repo or ChatTurnRepository()
        self._session_repo = session_repo or SessionRepository()
        self._chunk_repo = chunk_repo or ChunkRepository()
        self._document_service = document_service or DocumentService()
        self._upload_file = upload_file_fn or upload_file
        self._build_object_key = object_key_builder or build_object_key
        self._max_file_size_bytes = (
            max_file_size_bytes
            if max_file_size_bytes is not None
            else settings.document_max_file_size_bytes
        )

    async def create_turn(
        self,
        *,
        client_request_id: str,
        session_id: str,
        tenant_id: str,
        user_id: str,
        query: str,
        attachments: list[AttachmentDeclaration],
    ) -> ChatTurnView:
        normalized_query = query.strip()
        if not normalized_query:
            raise ChatTurnValidationError("问题不能为空")
        self._validate_attachment_list(attachments)

        session = await self._session_repo.get_by_id(session_id, tenant_id, user_id)
        if session is None:
            raise ChatTurnNotFoundError("会话不存在")
        if not session.space_id:
            raise ChatTurnConflictError("当前会话没有绑定知识空间")

        try:
            return await self._turn_repo.create_turn(
                client_request_id=client_request_id,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                space_id=session.space_id,
                query=normalized_query,
                attachments=attachments,
            )
        except ValueError as exc:
            raise ChatTurnNotFoundError("会话不存在") from exc

    async def get_turn(
        self, *, turn_id: str, tenant_id: str, user_id: str
    ) -> ChatTurnView:
        turn = await self._turn_repo.get_turn(
            turn_id=turn_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if turn is None:
            raise ChatTurnNotFoundError("聊天轮次不存在")
        return turn

    async def upload_attachment(
        self,
        *,
        turn_id: str,
        attachment_id: str,
        tenant_id: str,
        user_id: str,
        file_name: str,
        mime_type: str,
        data: bytes,
    ) -> AttachmentUploadResult:
        turn = await self.get_turn(turn_id=turn_id, tenant_id=tenant_id, user_id=user_id)
        attachment = self._find_attachment(turn, attachment_id)

        # 重复 PUT 不创建第二份文档，也不再次启动 Workflow。
        if attachment.document_id:
            return AttachmentUploadResult(attachment.document_id, None, None)
        if turn.processing_status is not TurnProcessingStatus.WAITING_FILES:
            raise ChatTurnConflictError("当前聊天轮次不再接受附件上传")

        self._validate_actual_file(attachment, file_name, mime_type, data)
        document_id = str(uuid.uuid4())
        object_key = self._build_object_key(
            tenant_id, turn.space_id, document_id, attachment.file_name
        )

        try:
            await self._upload_file(data, object_key, content_type=mime_type)
            await self._chunk_repo.create_document(
                document_id=document_id,
                tenant_id=tenant_id,
                space_id=turn.space_id,
                original_name=attachment.file_name,
                mime_type=mime_type,
                file_path=object_key,
                file_size=len(data),
            )
            linked = await self._turn_repo.link_document(
                turn_id=turn_id,
                attachment_id=attachment_id,
                document_id=document_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            if not linked:
                current = await self.get_turn(
                    turn_id=turn_id, tenant_id=tenant_id, user_id=user_id
                )
                current_attachment = self._find_attachment(current, attachment_id)
                if current_attachment.document_id:
                    return AttachmentUploadResult(
                        current_attachment.document_id, None, None
                    )
                raise ChatTurnConflictError("附件状态已经变化，请刷新后重试")
        except ChatTurnError:
            raise
        except Exception as exc:
            await self._turn_repo.mark_upload_failed(
                turn_id=turn_id,
                attachment_id=attachment_id,
                tenant_id=tenant_id,
                user_id=user_id,
                error_message="文件上传失败，请重试",
            )
            raise ChatTurnConflictError("文件上传失败，请重试") from exc

        result = await self._document_service.ingest(IngestRequest(
            tenant_id=tenant_id,
            space_id=turn.space_id,
            file_path=object_key,
            mime_type=mime_type,
            original_filename=attachment.file_name,
            document_id=document_id,
        ))
        return AttachmentUploadResult(
            document_id=result.document_id,
            task_id=result.task_id,
            workflow_run_id=result.workflow_run_id,
        )

    async def retry_attachment(
        self,
        *,
        turn_id: str,
        attachment_id: str,
        tenant_id: str,
        user_id: str,
    ) -> IngestResult:
        turn = await self.get_turn(turn_id=turn_id, tenant_id=tenant_id, user_id=user_id)
        attachment = self._find_attachment(turn, attachment_id)
        if (
            attachment.status is not AttachmentStatus.FAILED
            or not attachment.document_id
        ):
            raise ChatTurnConflictError("只有入库失败且已上传的附件可以直接重试")
        return await self._document_service.retry_ingestion(
            attachment.document_id, tenant_id
        )

    async def set_ignored(
        self,
        *,
        turn_id: str,
        attachment_id: str,
        tenant_id: str,
        user_id: str,
        ignored: bool,
    ) -> ChatTurnView:
        turn = await self.get_turn(turn_id=turn_id, tenant_id=tenant_id, user_id=user_id)
        self._find_attachment(turn, attachment_id)
        if turn.processing_status not in {
            TurnProcessingStatus.WAITING_FILES,
            TurnProcessingStatus.ANSWER_FAILED,
        }:
            raise ChatTurnConflictError("当前聊天轮次不能修改附件")
        changed = await self._turn_repo.set_ignored(
            turn_id=turn_id,
            attachment_id=attachment_id,
            tenant_id=tenant_id,
            user_id=user_id,
            ignored=ignored,
        )
        if not changed:
            raise ChatTurnNotFoundError("附件不存在")
        return await self.get_turn(turn_id=turn_id, tenant_id=tenant_id, user_id=user_id)

    async def add_attachment(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
        attachment: AttachmentDeclaration,
    ) -> ChatTurnView:
        self._validate_declaration(attachment)
        turn = await self.get_turn(turn_id=turn_id, tenant_id=tenant_id, user_id=user_id)
        if turn.processing_status is not TurnProcessingStatus.WAITING_FILES:
            raise ChatTurnConflictError("当前聊天轮次不能补充附件")
        if turn.readiness not in {TurnReadiness.BLOCKED, TurnReadiness.EMPTY}:
            raise ChatTurnConflictError("只有附件失败或全部忽略后才能补充附件")
        if len(turn.attachments) >= MAX_ATTACHMENTS:
            raise ChatTurnConflictError(f"每条消息最多 {MAX_ATTACHMENTS} 个附件")

        added = await self._turn_repo.add_attachment(
            turn_id=turn_id,
            tenant_id=tenant_id,
            user_id=user_id,
            attachment=attachment,
            max_attachments=MAX_ATTACHMENTS,
        )
        if not added:
            raise ChatTurnConflictError("附件未添加，轮次状态或附件数量已经变化")
        return await self.get_turn(turn_id=turn_id, tenant_id=tenant_id, user_id=user_id)

    async def cancel_turn(
        self, *, turn_id: str, tenant_id: str, user_id: str
    ) -> ChatTurnView:
        await self.get_turn(turn_id=turn_id, tenant_id=tenant_id, user_id=user_id)
        cancelled = await self._turn_repo.cancel_turn(
            turn_id=turn_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if not cancelled:
            raise ChatTurnConflictError("当前聊天轮次不能取消")
        return await self.get_turn(turn_id=turn_id, tenant_id=tenant_id, user_id=user_id)

    def _validate_attachment_list(
        self, attachments: list[AttachmentDeclaration]
    ) -> None:
        if not attachments:
            raise ChatTurnValidationError("至少需要一个附件")
        if len(attachments) > MAX_ATTACHMENTS:
            raise ChatTurnValidationError(f"每条消息最多 {MAX_ATTACHMENTS} 个附件")
        client_ids: set[str] = set()
        for attachment in attachments:
            self._validate_declaration(attachment)
            if attachment.client_id in client_ids:
                raise ChatTurnValidationError("附件 client_id 不能重复")
            client_ids.add(attachment.client_id)

    def _validate_declaration(self, attachment: AttachmentDeclaration) -> None:
        if not attachment.client_id.strip():
            raise ChatTurnValidationError("附件 client_id 不能为空")
        if not attachment.file_name.strip():
            raise ChatTurnValidationError("附件文件名不能为空")
        if attachment.mime_type not in ALLOWED_MIME_TYPES:
            raise ChatTurnValidationError("不支持该附件类型")
        if not 0 < attachment.file_size <= self._max_file_size_bytes:
            raise ChatTurnValidationError("附件大小超出允许范围")

    def _validate_actual_file(
        self,
        attachment: AttachmentView,
        file_name: str,
        mime_type: str,
        data: bytes,
    ) -> None:
        if file_name != attachment.file_name:
            raise ChatTurnValidationError("实际文件名与附件声明不一致")
        if mime_type != attachment.mime_type:
            raise ChatTurnValidationError("实际 MIME 类型与附件声明不一致")
        if len(data) != attachment.file_size:
            raise ChatTurnValidationError("实际文件大小与附件声明不一致")
        if not _content_matches_mime(data, mime_type):
            raise ChatTurnValidationError("文件内容与 MIME 类型不一致")

    @staticmethod
    def _find_attachment(turn: ChatTurnView, attachment_id: str) -> AttachmentView:
        for attachment in turn.attachments:
            if attachment.attachment_id == attachment_id:
                return attachment
        raise ChatTurnNotFoundError("附件不存在")


def _content_matches_mime(data: bytes, mime_type: str) -> bool:
    if mime_type == "application/pdf":
        return data.startswith(b"%PDF-")
    if mime_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        return data.startswith(b"PK\x03\x04")
    if mime_type in {
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }:
        return data.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
    if mime_type in {"text/plain", "text/html", "text/markdown"}:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    signatures = {
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/tiff": (b"II*\x00", b"MM\x00*"),
        "image/bmp": (b"BM",),
        "image/webp": (b"RIFF",),
    }
    prefixes = signatures.get(mime_type)
    if prefixes is None:
        return False
    if mime_type == "image/webp":
        return data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return any(data.startswith(prefix) for prefix in prefixes)
