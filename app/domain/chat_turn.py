from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class AttachmentStatus(StrEnum):
    """前端可见的统一附件状态。"""

    PENDING_UPLOAD = "pending_upload"
    INGESTING = "ingesting"
    INDEXED = "indexed"
    FAILED = "failed"
    IGNORED = "ignored"


class TurnReadiness(StrEnum):
    """根据本轮有效附件实时计算的回答就绪状态。"""

    INGESTING = "ingesting"
    BLOCKED = "blocked"
    READY = "ready"
    EMPTY = "empty"


class TurnProcessingStatus(StrEnum):
    """附件型用户消息的持久化处理状态。"""

    WAITING_FILES = "waiting_files"
    ANSWERING = "answering"
    COMPLETED = "completed"
    ANSWER_FAILED = "answer_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AttachmentDeclaration:
    """创建附件占位时由客户端声明、随后由上传接口重新校验的信息。"""

    client_id: str
    file_name: str
    mime_type: str
    file_size: int


@dataclass(frozen=True)
class AttachmentView:
    """结合附件关系与文档状态后返回给客户端的统一视图。"""

    attachment_id: str
    client_id: str
    file_name: str
    mime_type: str
    file_size: int
    document_id: str | None
    status: AttachmentStatus
    ignored: bool
    error_message: str | None


@dataclass(frozen=True)
class AssistantAnswerView:
    """已经持久化的 assistant 回答及其引用。"""

    message_id: str
    content: str
    citations: list[dict]


@dataclass(frozen=True)
class ChatTurnView:
    """一条附件型用户消息的完整可恢复状态。"""

    turn_id: str
    session_id: str
    space_id: str
    query: str
    readiness: TurnReadiness
    processing_status: TurnProcessingStatus
    processing_error: str | None
    attachments: list[AttachmentView]
    assistant: AssistantAnswerView | None


def compute_readiness(
    attachments: Sequence[AttachmentView],
) -> TurnReadiness:
    """计算本轮是否能够回答，不允许空附件回退到全空间检索。"""

    active = [
        attachment
        for attachment in attachments
        if not attachment.ignored and attachment.status is not AttachmentStatus.IGNORED
    ]
    if not active:
        return TurnReadiness.EMPTY
    if any(attachment.status is AttachmentStatus.FAILED for attachment in active):
        return TurnReadiness.BLOCKED
    if all(attachment.status is AttachmentStatus.INDEXED for attachment in active):
        return TurnReadiness.READY
    return TurnReadiness.INGESTING
