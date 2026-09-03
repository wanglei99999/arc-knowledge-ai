from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from app.domain.document import DocumentChunk, DocumentStatus
from app.infrastructure.postgres.client import get_session


def _row_to_dict(row) -> dict:
    """将数据库行转为 dict，UUID 对象统一转为 str。"""
    return {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in dict(row._mapping).items()}


class ChunkRepository:
    """
    DocumentChunk 持久化。

    Phase 0 用裸 SQL（asyncpg + SQLAlchemy text），
    Phase 1 可换成 SQLAlchemy ORM 或 SQLModel。
    """

    async def create_document(
        self,
        document_id: str,
        tenant_id: str,
        space_id: str,
        original_name: str,
        mime_type: str,
        file_path: str,
        file_size: int | None = None,
    ) -> None:
        """在 documents 表写入初始记录（status=pending），触发 Workflow 前调用。"""
        sql = text("""
            INSERT INTO documents
                (id, tenant_id, space_id, original_name, mime_type, file_path, file_size, status)
            VALUES
                (:id, :tenant_id, :space_id, :original_name, :mime_type, :file_path,
                 :file_size, 'pending')
            ON CONFLICT (id) DO NOTHING
        """)
        async with get_session() as session:
            await session.execute(
                sql,
                {
                    "id": document_id,
                    "tenant_id": tenant_id,
                    "space_id": space_id,
                    "original_name": original_name,
                    "mime_type": mime_type,
                    "file_path": file_path,
                    "file_size": file_size,
                },
            )

    async def save_chunks(self, chunks: list[DocumentChunk]) -> None:
        """批量 upsert chunks（按 chunk_id 冲突时更新）"""
        if not chunks:
            return

        rows = [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "tenant_id": c.tenant_id,
                "content": c.content,
                "chunk_index": c.chunk_index,
                "token_count": c.token_count,
                "metadata": json.dumps(c.metadata),
            }
            for c in chunks
        ]

        sql = text("""
            INSERT INTO document_chunks
                (chunk_id, document_id, tenant_id, content, chunk_index,
                 token_count, metadata, embedding_status, embedded_at)
            VALUES
                (:chunk_id, :document_id, :tenant_id, :content, :chunk_index,
                 :token_count, :metadata, 'current', NOW())
            ON CONFLICT (chunk_id) DO UPDATE SET
                content          = EXCLUDED.content,
                chunk_index      = EXCLUDED.chunk_index,
                token_count      = EXCLUDED.token_count,
                metadata         = EXCLUDED.metadata,
                embedding_status = 'current',
                embedded_at      = NOW(),
                updated_at       = NOW()
        """)

        async with get_session() as session:
            await session.execute(sql, rows)

    async def update_chunk_count(
        self,
        document_id: str,
        tenant_id: str,
        count: int,
    ) -> None:
        """更新 documents.chunk_count，入库完成后调用。"""
        sql = text("""
            UPDATE documents
            SET chunk_count = :count, updated_at = NOW()
            WHERE id        = :document_id
              AND tenant_id = :tenant_id
        """)
        async with get_session() as session:
            await session.execute(
                sql,
                {
                    "count": count,
                    "document_id": document_id,
                    "tenant_id": tenant_id,
                },
            )

    async def update_document_status(
        self,
        document_id: str,
        tenant_id: str,
        status: DocumentStatus,
        error_message: str | None = None,
    ) -> None:
        deleted_at_clause = ", deleted_at = NOW()" if status == DocumentStatus.DELETED else ""
        error_clause = ", error_message = :error_message" if error_message is not None else ""

        sql = text(f"""
            UPDATE documents
            SET status = :status,
                updated_at = NOW()
                {deleted_at_clause}
                {error_clause}
            WHERE id = :document_id
            AND tenant_id = :tenant_id
        """)

        params: dict = {
            "status": status.value,
            "document_id": document_id,
            "tenant_id": tenant_id,
        }

        if error_message is not None:
            params["error_message"] = error_message

        async with get_session() as session:
            await session.execute(sql, params)

    async def get_chunks_by_ids(
        self,
        chunk_ids: list[str],
        tenant_id: str,
    ) -> list[dict]:
        """按 chunk_id 列表批量查询，保留顺序（ORDER BY chunk_index）。"""
        if not chunk_ids:
            return []
        sql = text("""
            SELECT dc.chunk_id, dc.content, dc.document_id, dc.chunk_index,
                   dc.token_count, dc.metadata, d.original_name
            FROM document_chunks dc
            LEFT JOIN documents d ON dc.document_id = d.id
            WHERE dc.chunk_id  = ANY(:chunk_ids)
              AND dc.tenant_id = :tenant_id
            ORDER BY dc.chunk_index
        """)
        async with get_session() as session:
            result = await session.execute(
                sql,
                {
                    "chunk_ids": chunk_ids,
                    "tenant_id": tenant_id,
                },
            )
            return [_row_to_dict(row) for row in result]

    async def get_chunks_by_document(
        self,
        document_id: str,
        tenant_id: str,
    ) -> list[dict]:
        sql = text("""
            SELECT chunk_id, content, chunk_index, token_count, metadata
            FROM document_chunks
            WHERE document_id = :document_id
              AND tenant_id   = :tenant_id
            ORDER BY chunk_index
        """)
        async with get_session() as session:
            result = await session.execute(
                sql,
                {
                    "document_id": document_id,
                    "tenant_id": tenant_id,
                },
            )
            return [_row_to_dict(row) for row in result]

    async def get_document_meta(
        self,
        document_id: str,
        tenant_id: str,
    ) -> dict | None:
        """查询文档元数据，主要用于获取 file_path（MinIO object key）"""
        sql = text("""
            SELECT id AS document_id, tenant_id, space_id, file_path,
                   original_name, mime_type, file_size, status
            FROM documents
            WHERE id         = :document_id
              AND tenant_id  = :tenant_id
        """)
        async with get_session() as session:
            result = await session.execute(
                sql,
                {
                    "document_id": document_id,
                    "tenant_id": tenant_id,
                },
            )
            row = result.one_or_none()
            return _row_to_dict(row) if row else None

    async def reset_failed_document(
        self,
        document_id: str,
        tenant_id: str,
    ) -> bool:
        """仅把 failed 文档重置为 pending，供清理残留索引后的重试使用。"""
        sql = text("""
            UPDATE documents
            SET status = 'pending',
                chunk_count = 0,
                error_message = NULL,
                updated_at = NOW()
            WHERE id = :document_id
              AND tenant_id = :tenant_id
              AND status = 'failed'
            RETURNING id
        """)
        async with get_session() as session:
            result = await session.execute(
                sql,
                {
                    "document_id": document_id,
                    "tenant_id": tenant_id,
                },
            )
            return result.one_or_none() is not None

    async def list_documents(
        self,
        tenant_id: str,
        space_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """查询文档列表，按创建时间倒序。"""
        where = "WHERE tenant_id = :tenant_id AND status != 'deleted'"
        params: dict = {"tenant_id": tenant_id, "limit": limit, "offset": offset}
        if space_id:
            where += " AND space_id = :space_id"
            params["space_id"] = space_id

        sql = text(f"""
            SELECT id, tenant_id, space_id, original_name, mime_type,
                   status, chunk_count, error_message, created_at, updated_at
            FROM documents
            {where}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """)
        async with get_session() as session:
            result = await session.execute(sql, params)
            return [_row_to_dict(row) for row in result]

    async def count_documents(
        self,
        tenant_id: str,
        space_id: str | None = None,
    ) -> int:
        where = "WHERE tenant_id = :tenant_id AND status != 'deleted'"
        params: dict = {"tenant_id": tenant_id}
        if space_id:
            where += " AND space_id = :space_id"
            params["space_id"] = space_id

        sql = text(f"SELECT COUNT(*) FROM documents {where}")
        async with get_session() as session:
            result = await session.execute(sql, params)
            return result.scalar() or 0

    async def delete_chunks_by_document(
        self,
        document_id: str,
        tenant_id: str,
    ) -> None:
        """物理删除指定文档的所有 chunks"""
        sql = text("""
            DELETE FROM document_chunks
            WHERE document_id = :document_id
              AND tenant_id   = :tenant_id
        """)
        async with get_session() as session:
            await session.execute(
                sql,
                {
                    "document_id": document_id,
                    "tenant_id": tenant_id,
                },
            )
