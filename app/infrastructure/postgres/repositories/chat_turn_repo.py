from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.domain.chat_turn import (
    AnswerClaim,
    AssistantAnswerView,
    AttachmentDeclaration,
    AttachmentStatus,
    AttachmentView,
    ChatTurnView,
    TurnProcessingStatus,
    compute_readiness,
)
from app.infrastructure.postgres.client import get_session


class ChatTurnRepository:
    """附件型聊天轮次的事务、读取和状态变更。"""

    async def create_turn(
        self,
        *,
        client_request_id: str,
        session_id: str,
        tenant_id: str,
        user_id: str,
        space_id: str,
        query: str,
        attachments: list[AttachmentDeclaration],
    ) -> ChatTurnView:
        async with get_session() as db:
            session_result = await db.execute(text("""
                SELECT session_id
                FROM sessions
                WHERE session_id = :session_id
                  AND tenant_id = :tenant_id
                  AND user_id = :user_id
                  AND space_id = :space_id
                FOR UPDATE
            """), {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "space_id": space_id,
            })
            if session_result.one_or_none() is None:
                raise ValueError("Session not found for the selected space")

            existing_result = await db.execute(text("""
                SELECT message_id
                FROM messages
                WHERE client_request_id = :client_request_id
                  AND tenant_id = :tenant_id
                  AND user_id = :user_id
            """), {
                "client_request_id": client_request_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
            })
            existing = existing_result.one_or_none()
            if existing is not None:
                turn = await self._get_turn_with_db(
                    db,
                    turn_id=str(existing._mapping["message_id"]),
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                if turn is None:
                    raise RuntimeError("Existing chat turn could not be hydrated")
                return turn

            turn_id = str(uuid.uuid4())
            await db.execute(text("""
                INSERT INTO messages (
                    message_id, session_id, tenant_id, user_id, role, content,
                    processing_status, processing_error, client_request_id
                )
                VALUES (
                    :message_id, :session_id, :tenant_id, :user_id, 'user', :content,
                    :processing_status, NULL, :client_request_id
                )
            """), {
                "message_id": turn_id,
                "session_id": session_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "content": query,
                "processing_status": TurnProcessingStatus.WAITING_FILES.value,
                "client_request_id": client_request_id,
            })

            attachment_rows = [
                {
                    "attachment_id": str(uuid.uuid4()),
                    "message_id": turn_id,
                    "tenant_id": tenant_id,
                    "space_id": space_id,
                    "client_id": attachment.client_id,
                    "file_name": attachment.file_name,
                    "mime_type": attachment.mime_type,
                    "file_size": attachment.file_size,
                }
                for attachment in attachments
            ]
            if attachment_rows:
                await db.execute(text("""
                    INSERT INTO message_attachments (
                        attachment_id, message_id, tenant_id, space_id,
                        client_id, file_name, mime_type, file_size
                    )
                    VALUES (
                        :attachment_id, :message_id, :tenant_id, :space_id,
                        :client_id, :file_name, :mime_type, :file_size
                    )
                """), attachment_rows)

            await db.execute(text("""
                UPDATE sessions
                SET message_count = message_count + 1, updated_at = NOW()
                WHERE session_id = :session_id
                  AND tenant_id = :tenant_id
                  AND user_id = :user_id
            """), {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
            })

            turn = await self._get_turn_with_db(
                db,
                turn_id=turn_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            if turn is None:
                raise RuntimeError("New chat turn could not be hydrated")
            return turn

    async def get_turn(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
    ) -> ChatTurnView | None:
        async with get_session() as db:
            return await self._get_turn_with_db(
                db,
                turn_id=turn_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )

    async def add_attachment(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
        attachment: AttachmentDeclaration,
        max_attachments: int,
    ) -> bool:
        """在锁住本轮消息后增加附件占位，避免并发请求突破数量上限。"""
        async with get_session() as db:
            turn_result = await db.execute(text("""
                SELECT m.message_id, s.space_id
                FROM messages AS m
                JOIN sessions AS s ON s.session_id = m.session_id
                WHERE m.message_id = :turn_id
                  AND m.tenant_id = :tenant_id
                  AND m.user_id = :user_id
                  AND s.tenant_id = :tenant_id
                  AND s.user_id = :user_id
                  AND m.processing_status = 'waiting_files'
                FOR UPDATE
            """), {
                "turn_id": turn_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
            })
            turn_row = turn_result.one_or_none()
            if turn_row is None:
                return False

            existing_result = await db.execute(text("""
                SELECT attachment_id
                FROM message_attachments
                WHERE message_id = :turn_id
                  AND tenant_id = :tenant_id
                  AND client_id = :client_id
            """), {
                "turn_id": turn_id,
                "tenant_id": tenant_id,
                "client_id": attachment.client_id,
            })
            if existing_result.one_or_none() is not None:
                return True

            count_result = await db.execute(text("""
                SELECT COUNT(*) AS attachment_count
                FROM message_attachments
                WHERE message_id = :turn_id
                  AND tenant_id = :tenant_id
            """), {
                "turn_id": turn_id,
                "tenant_id": tenant_id,
            })
            count_row = count_result.one()
            if int(count_row._mapping["attachment_count"]) >= max_attachments:
                return False

            result = await db.execute(text("""
                INSERT INTO message_attachments (
                    attachment_id, message_id, tenant_id, space_id,
                    client_id, file_name, mime_type, file_size
                )
                VALUES (
                    :attachment_id, :message_id, :tenant_id, :space_id,
                    :client_id, :file_name, :mime_type, :file_size
                )
                ON CONFLICT (message_id, client_id) DO NOTHING
                RETURNING attachment_id
            """), {
                "attachment_id": str(uuid.uuid4()),
                "message_id": turn_id,
                "tenant_id": tenant_id,
                "space_id": str(turn_row._mapping["space_id"]),
                "client_id": attachment.client_id,
                "file_name": attachment.file_name,
                "mime_type": attachment.mime_type,
                "file_size": attachment.file_size,
            })
            return result.one_or_none() is not None

    async def link_document(
        self,
        *,
        turn_id: str,
        attachment_id: str,
        document_id: str,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        sql = text("""
            UPDATE message_attachments AS ma
            SET document_id = :document_id,
                upload_status = 'uploaded',
                upload_error = NULL,
                updated_at = NOW()
            FROM messages AS m, sessions AS s, documents AS d
            WHERE ma.attachment_id = :attachment_id
              AND ma.message_id = :turn_id
              AND ma.message_id = m.message_id
              AND m.session_id = s.session_id
              AND d.id = :document_id
              AND d.tenant_id = :tenant_id
              AND d.space_id = ma.space_id
              AND ma.tenant_id = :tenant_id
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
              AND (ma.document_id IS NULL OR ma.document_id = :document_id)
            RETURNING ma.attachment_id
        """)
        return await self._execute_owned_mutation(sql, {
            "turn_id": turn_id,
            "attachment_id": attachment_id,
            "document_id": document_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
        })

    async def mark_upload_failed(
        self,
        *,
        turn_id: str,
        attachment_id: str,
        tenant_id: str,
        user_id: str,
        error_message: str,
    ) -> bool:
        sql = text("""
            UPDATE message_attachments AS ma
            SET upload_status = 'failed',
                upload_error = :error_message,
                updated_at = NOW()
            FROM messages AS m, sessions AS s
            WHERE ma.attachment_id = :attachment_id
              AND ma.message_id = :turn_id
              AND ma.message_id = m.message_id
              AND m.session_id = s.session_id
              AND ma.tenant_id = :tenant_id
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
              AND ma.document_id IS NULL
              AND ma.upload_status IN ('pending', 'failed')
            RETURNING ma.attachment_id
        """)
        return await self._execute_owned_mutation(sql, {
            "turn_id": turn_id,
            "attachment_id": attachment_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "error_message": error_message,
        })

    async def set_ignored(
        self,
        *,
        turn_id: str,
        attachment_id: str,
        tenant_id: str,
        user_id: str,
        ignored: bool,
    ) -> bool:
        sql = text("""
            UPDATE message_attachments AS ma
            SET ignored = :ignored, updated_at = NOW()
            FROM messages AS m, sessions AS s
            WHERE ma.attachment_id = :attachment_id
              AND ma.message_id = :turn_id
              AND ma.message_id = m.message_id
              AND m.session_id = s.session_id
              AND ma.tenant_id = :tenant_id
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
            RETURNING ma.attachment_id
        """)
        return await self._execute_owned_mutation(sql, {
            "turn_id": turn_id,
            "attachment_id": attachment_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "ignored": ignored,
        })

    async def cancel_turn(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        sql = text("""
            UPDATE messages AS m
            SET processing_status = 'cancelled', processing_error = NULL
            FROM sessions AS s
            WHERE m.message_id = :turn_id
              AND m.session_id = s.session_id
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
              AND m.processing_status IN ('waiting_files', 'answer_failed')
            RETURNING m.message_id
        """)
        return await self._execute_owned_mutation(sql, {
            "turn_id": turn_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
        })

    async def claim_answer(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        sql = text("""
            UPDATE messages AS m
            SET processing_status = 'answering', processing_error = NULL
            FROM sessions AS s
            WHERE m.message_id = :turn_id
              AND m.session_id = s.session_id
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
              AND m.processing_status IN ('waiting_files', 'answer_failed')
            RETURNING m.message_id
        """)
        return await self._execute_owned_mutation(sql, {
            "turn_id": turn_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
        })

    async def claim_ready_answer(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
    ) -> AnswerClaim | None:
        """仅在所有有效附件均已入库时抢占回答，并返回可信文档范围。"""
        params = {
            "turn_id": turn_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
        }
        async with get_session() as db:
            result = await db.execute(text("""
                UPDATE messages AS m
                SET processing_status = 'answering', processing_error = NULL
                FROM sessions AS s
                WHERE m.message_id = :turn_id
                  AND m.session_id = s.session_id
                  AND m.role = 'user'
                  AND m.tenant_id = :tenant_id
                  AND m.user_id = :user_id
                  AND s.tenant_id = :tenant_id
                  AND s.user_id = :user_id
                  AND m.processing_status IN ('waiting_files', 'answer_failed')
                  AND EXISTS (
                      SELECT 1
                      FROM message_attachments AS ma
                      JOIN documents AS d
                        ON d.id = ma.document_id
                       AND d.tenant_id = ma.tenant_id
                       AND d.space_id = ma.space_id
                      WHERE ma.message_id = m.message_id
                        AND ma.tenant_id = m.tenant_id
                        AND ma.ignored = FALSE
                        AND ma.upload_status = 'uploaded'
                        AND d.status = 'indexed'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM message_attachments AS ma
                      LEFT JOIN documents AS d
                        ON d.id = ma.document_id
                       AND d.tenant_id = ma.tenant_id
                       AND d.space_id = ma.space_id
                      WHERE ma.message_id = m.message_id
                        AND ma.tenant_id = m.tenant_id
                        AND ma.ignored = FALSE
                        AND (
                            ma.upload_status <> 'uploaded'
                            OR ma.document_id IS NULL
                            OR d.id IS NULL
                            OR d.status <> 'indexed'
                        )
                  )
                RETURNING m.message_id, m.session_id, s.space_id, m.content
            """), params)
            row = result.one_or_none()
            if row is None:
                return None

            scope_result = await db.execute(text("""
                SELECT DISTINCT ma.document_id
                FROM message_attachments AS ma
                JOIN documents AS d
                  ON d.id = ma.document_id
                 AND d.tenant_id = ma.tenant_id
                 AND d.space_id = ma.space_id
                WHERE ma.message_id = :turn_id
                  AND ma.tenant_id = :tenant_id
                  AND ma.ignored = FALSE
                  AND ma.upload_status = 'uploaded'
                  AND d.status = 'indexed'
                ORDER BY ma.document_id
            """), params)
            document_ids = [
                str(document_row._mapping["document_id"])
                for document_row in scope_result.fetchall()
            ]
            if not document_ids:
                raise RuntimeError("回答文档范围在抢占过程中发生变化")
            mapping = row._mapping
            return AnswerClaim(
                turn_id=str(mapping["message_id"]),
                session_id=str(mapping["session_id"]),
                tenant_id=tenant_id,
                user_id=user_id,
                space_id=str(mapping["space_id"]),
                query=mapping["content"],
                document_ids=document_ids,
            )

    async def fail_answer(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
        error_message: str,
    ) -> bool:
        sql = text("""
            UPDATE messages AS m
            SET processing_status = 'answer_failed',
                processing_error = :error_message
            FROM sessions AS s
            WHERE m.message_id = :turn_id
              AND m.session_id = s.session_id
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
              AND m.processing_status = 'answering'
            RETURNING m.message_id
        """)
        return await self._execute_owned_mutation(sql, {
            "turn_id": turn_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "error_message": error_message,
        })

    async def _execute_owned_mutation(self, sql, params: dict[str, object]) -> bool:
        async with get_session() as db:
            result = await db.execute(sql, params)
            return result.one_or_none() is not None

    async def _get_turn_with_db(
        self,
        db: Any,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
    ) -> ChatTurnView | None:
        params = {
            "turn_id": turn_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
        }
        turn_result = await db.execute(text("""
            SELECT m.message_id, m.session_id, m.tenant_id, m.user_id,
                   s.space_id, m.content, m.processing_status,
                   m.processing_error, m.created_at
            FROM messages AS m
            JOIN sessions AS s ON s.session_id = m.session_id
            WHERE m.message_id = :turn_id
              AND m.role = 'user'
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
        """), params)
        turn_row = turn_result.one_or_none()
        if turn_row is None:
            return None

        attachment_result = await db.execute(text("""
            SELECT ma.attachment_id, ma.client_id, ma.file_name, ma.mime_type,
                   ma.file_size, ma.document_id, ma.upload_status,
                   ma.upload_error, ma.ignored,
                   d.status AS document_status,
                   d.error_message AS document_error
            FROM message_attachments AS ma
            JOIN messages AS m ON m.message_id = ma.message_id
            JOIN sessions AS s ON s.session_id = m.session_id
            LEFT JOIN documents AS d
              ON d.id = ma.document_id AND d.tenant_id = ma.tenant_id
            WHERE ma.message_id = :turn_id
              AND ma.tenant_id = :tenant_id
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
            ORDER BY ma.created_at, ma.attachment_id
        """), params)
        attachments = [
            _row_to_attachment(row)
            for row in attachment_result.fetchall()
        ]

        assistant_result = await db.execute(text("""
            SELECT a.message_id, a.content
            FROM messages AS a
            JOIN messages AS m ON m.session_id = a.session_id
            JOIN sessions AS s ON s.session_id = m.session_id
            WHERE m.message_id = :turn_id
              AND m.tenant_id = :tenant_id
              AND m.user_id = :user_id
              AND s.tenant_id = :tenant_id
              AND s.user_id = :user_id
              AND a.role = 'assistant'
              AND a.created_at >= m.created_at
              AND NOT EXISTS (
                  SELECT 1
                  FROM messages AS next_user
                  WHERE next_user.session_id = m.session_id
                    AND next_user.role = 'user'
                    AND next_user.created_at > m.created_at
                    AND next_user.created_at < a.created_at
              )
            ORDER BY a.created_at ASC
            LIMIT 1
        """), params)
        assistant_row = assistant_result.one_or_none()
        assistant = None
        if assistant_row is not None:
            assistant_mapping = assistant_row._mapping
            assistant_id = str(assistant_mapping["message_id"])
            citation_result = await db.execute(text("""
                SELECT document_id, chunk_id, rank, score, retrieval_mode,
                       content_snapshot, title_snapshot
                FROM message_citations
                WHERE message_id = :assistant_message_id
                  AND tenant_id = :tenant_id
                ORDER BY rank
            """), {
                "assistant_message_id": assistant_id,
                "tenant_id": tenant_id,
            })
            citations = [_row_to_citation(row) for row in citation_result.fetchall()]
            assistant = AssistantAnswerView(
                message_id=assistant_id,
                content=assistant_mapping["content"],
                citations=citations,
            )

        mapping = turn_row._mapping
        return ChatTurnView(
            turn_id=str(mapping["message_id"]),
            session_id=str(mapping["session_id"]),
            space_id=str(mapping["space_id"]),
            query=mapping["content"],
            readiness=compute_readiness(attachments),
            processing_status=TurnProcessingStatus(mapping["processing_status"]),
            processing_error=mapping.get("processing_error"),
            attachments=attachments,
            assistant=assistant,
        )


def _row_to_attachment(row: Any) -> AttachmentView:
    mapping = row._mapping
    ignored = bool(mapping["ignored"])
    upload_status = mapping["upload_status"]
    document_status = mapping.get("document_status")

    if ignored:
        status = AttachmentStatus.IGNORED
        error_message = None
    elif upload_status == "failed":
        status = AttachmentStatus.FAILED
        error_message = mapping.get("upload_error")
    elif upload_status == "pending":
        status = AttachmentStatus.PENDING_UPLOAD
        error_message = None
    elif document_status == "indexed":
        status = AttachmentStatus.INDEXED
        error_message = None
    elif document_status == "failed":
        status = AttachmentStatus.FAILED
        error_message = mapping.get("document_error")
    else:
        status = AttachmentStatus.INGESTING
        error_message = None

    document_id = mapping.get("document_id")
    return AttachmentView(
        attachment_id=str(mapping["attachment_id"]),
        client_id=mapping["client_id"],
        file_name=mapping["file_name"],
        mime_type=mapping["mime_type"],
        file_size=mapping["file_size"],
        document_id=str(document_id) if document_id else None,
        status=status,
        ignored=ignored,
        error_message=error_message,
    )


def _row_to_citation(row: Any) -> dict:
    mapping = row._mapping
    document_id = mapping.get("document_id")
    chunk_id = mapping.get("chunk_id")
    return {
        "doc_id": str(document_id) if document_id else None,
        "chunk_id": str(chunk_id) if chunk_id else None,
        "doc_name": mapping.get("title_snapshot"),
        "content": mapping.get("content_snapshot"),
        "score": mapping.get("score"),
        "source": mapping.get("retrieval_mode"),
        "rank": mapping.get("rank"),
    }
