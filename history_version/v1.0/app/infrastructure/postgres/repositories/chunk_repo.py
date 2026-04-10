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
