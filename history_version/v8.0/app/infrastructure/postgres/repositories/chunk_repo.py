from __future__ import annotations

import json

from sqlalchemy import text

from app.domain.document import DocumentChunk, DocumentStatus
from app.infrastructure.postgres.client import get_session


class ChunkRepository:
    """
    DocumentChunk 持久化。

    Phase 0 用裸 SQL（asyncpg + SQLAlchemy text），
    Phase 1 可换成 SQLAlchemy ORM 或 SQLModel。
    """

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
                "embedding": c.embedding,   # pgvector: list[float] 直接传
            }
            for c in chunks
        ]

        sql = text("""
            INSERT INTO document_chunk
                (chunk_id, document_id, tenant_id, content, chunk_index,
                 token_count, metadata, embedding)
            VALUES
                (:chunk_id, :document_id, :tenant_id, :content, :chunk_index,
                 :token_count, :metadata::jsonb, :embedding)
            ON CONFLICT (chunk_id) DO UPDATE SET
                content      = EXCLUDED.content,
                chunk_index  = EXCLUDED.chunk_index,
                token_count  = EXCLUDED.token_count,
                metadata     = EXCLUDED.metadata,
                embedding    = EXCLUDED.embedding,
                updated_at   = NOW()
        """)

        async with get_session() as session:
            await session.execute(sql, rows)

    async def update_document_status(
        self,
        document_id: str,
        tenant_id: str,
        status: DocumentStatus,
    ) -> None:
        sql = text("""
            UPDATE document
            SET status = :status, updated_at = NOW()
            WHERE document_id = :document_id
              AND tenant_id   = :tenant_id
        """)
        async with get_session() as session:
            await session.execute(sql, {
                "status": status.value,
                "document_id": document_id,
                "tenant_id": tenant_id,
            })

    async def get_chunks_by_ids(
        self,
        chunk_ids: list[str],
        tenant_id: str,
    ) -> list[dict]:
        """按 chunk_id 列表批量查询，保留顺序（ORDER BY chunk_index）。"""
        if not chunk_ids:
            return []
        sql = text("""
            SELECT chunk_id, content, document_id, chunk_index, token_count, metadata
            FROM document_chunk
            WHERE chunk_id  = ANY(:chunk_ids)
              AND tenant_id = :tenant_id
            ORDER BY chunk_index
        """)
        async with get_session() as session:
            result = await session.execute(sql, {
                "chunk_ids": chunk_ids,
                "tenant_id": tenant_id,
            })
            return [dict(row._mapping) for row in result]

    async def get_chunks_by_document(
        self,
        document_id: str,
        tenant_id: str,
    ) -> list[dict]:
        sql = text("""
            SELECT chunk_id, content, chunk_index, token_count, metadata
            FROM document_chunk
            WHERE document_id = :document_id
              AND tenant_id   = :tenant_id
            ORDER BY chunk_index
        """)
        async with get_session() as session:
            result = await session.execute(sql, {
                "document_id": document_id,
                "tenant_id": tenant_id,
            })
            return [dict(row._mapping) for row in result]

    async def get_document_meta(
        self,
        document_id: str,
        tenant_id: str,
    ) -> dict | None:
        """查询文档元数据，主要用于获取 file_path（MinIO object key）"""
        sql = text("""
            SELECT document_id, tenant_id, file_path, status
            FROM document
            WHERE document_id = :document_id
              AND tenant_id   = :tenant_id
        """)
        async with get_session() as session:
            result = await session.execute(sql, {
                "document_id": document_id,
                "tenant_id": tenant_id,
            })
            row = result.one_or_none()
            return dict(row._mapping) if row else None

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
            return [dict(row._mapping) for row in result]

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
            DELETE FROM document_chunk
            WHERE document_id = :document_id
              AND tenant_id   = :tenant_id
        """)
        async with get_session() as session:
            await session.execute(sql, {
                "document_id": document_id,
                "tenant_id": tenant_id,
            })
