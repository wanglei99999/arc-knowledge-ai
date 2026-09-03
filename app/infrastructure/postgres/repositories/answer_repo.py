from __future__ import annotations

import uuid

from sqlalchemy import text

from app.domain.memory import Message
from app.infrastructure.postgres.client import get_session as get_db


def _optional_uuid(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


class AnswerRepository:
    """在单一事务中持久化回答、引用以及来源 Turn 的完成状态。"""

    async def save_assistant_with_citations(
        self,
        *,
        source_turn_id: str | None,
        session_id: str,
        tenant_id: str,
        user_id: str,
        content: str,
        space_id: str | None,
        citations: list[dict],
    ) -> Message:
        message_id = str(uuid.uuid4())
        async with get_db() as db:
            message_result = await db.execute(
                text("""
                INSERT INTO messages (
                    message_id, session_id, tenant_id, user_id,
                    role, content, token_count
                )
                SELECT :message_id, s.session_id, s.tenant_id, s.user_id,
                       'assistant', :content, 0
                FROM sessions AS s
                WHERE s.session_id = :session_id
                  AND s.tenant_id = :tenant_id
                  AND s.user_id = :user_id
                RETURNING message_id, session_id, tenant_id, user_id,
                          role, content, token_count, created_at
            """),
                {
                    "message_id": message_id,
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "role": "assistant",
                    "content": content,
                    "token_count": 0,
                },
            )
            message_row = message_result.one_or_none()
            if message_row is None:
                raise ValueError("会话不存在或无权访问")

            if citations:
                await db.execute(
                    text("""
                    INSERT INTO message_citations (
                        id, message_id, tenant_id, space_id,
                        document_id, chunk_id, rank, score,
                        retrieval_mode, content_snapshot, title_snapshot
                    )
                    VALUES (
                        :id, :message_id, :tenant_id, :space_id,
                        :document_id, :chunk_id, :rank, :score,
                        :retrieval_mode, :content_snapshot, :title_snapshot
                    )
                """),
                    [
                        {
                            "id": str(uuid.uuid4()),
                            "message_id": message_id,
                            "tenant_id": tenant_id,
                            "space_id": _optional_uuid(space_id),
                            "document_id": _optional_uuid(item.get("doc_id")),
                            "chunk_id": _optional_uuid(item.get("chunk_id")),
                            "rank": index + 1,
                            "score": item.get("score"),
                            "retrieval_mode": item.get("source", "unknown"),
                            "content_snapshot": item.get("content", ""),
                            "title_snapshot": item.get("doc_name"),
                        }
                        for index, item in enumerate(citations)
                    ],
                )

            session_result = await db.execute(
                text("""
                UPDATE sessions
                SET message_count = message_count + 1, updated_at = NOW()
                WHERE session_id = :session_id
                  AND tenant_id = :tenant_id
                  AND user_id = :user_id
                RETURNING session_id
            """),
                {
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                },
            )
            if session_result.one_or_none() is None:
                raise RuntimeError("会话消息计数更新失败")

            if source_turn_id is not None:
                completed_result = await db.execute(
                    text("""
                    UPDATE messages AS m
                    SET processing_status = 'completed', processing_error = NULL
                    FROM sessions AS s
                    WHERE m.message_id = :source_turn_id
                      AND m.session_id = :session_id
                      AND m.session_id = s.session_id
                      AND m.role = 'user'
                      AND m.tenant_id = :tenant_id
                      AND m.user_id = :user_id
                      AND s.tenant_id = :tenant_id
                      AND s.user_id = :user_id
                      AND m.processing_status = 'answering'
                    RETURNING m.message_id
                """),
                    {
                        "source_turn_id": source_turn_id,
                        "session_id": session_id,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                    },
                )
                if completed_result.one_or_none() is None:
                    raise RuntimeError("聊天轮次完成状态更新失败")

        mapping = message_row._mapping
        return Message(
            message_id=str(mapping["message_id"]),
            session_id=str(mapping["session_id"]),
            tenant_id=mapping["tenant_id"],
            user_id=mapping["user_id"],
            role=mapping["role"],
            content=mapping["content"],
            token_count=mapping.get("token_count", 0),
            created_at=mapping["created_at"],
        )
