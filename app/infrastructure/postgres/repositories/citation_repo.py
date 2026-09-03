from __future__ import annotations

import uuid

from sqlalchemy import text

from app.infrastructure.postgres.client import get_session as get_db


def _valid_uuid(val: str | None) -> str | None:
    """返回有效 UUID 字符串，无效值（如 'default'）返回 None。"""
    if val is None:
        return None
    try:
        uuid.UUID(str(val))
        return str(val)
    except ValueError:
        return None


class CitationRepository:
    """message_citations 表的持久化操作"""

    async def save(
        self,
        message_id: str,
        tenant_id: str,
        space_id: str | None,
        citations: list[dict],
    ) -> None:
        """批量保存一次回答的 RAG 引用记录。

        citations 中每个 dict 包含：
            doc_id, chunk_id, doc_name, chunk_index, content, score, source
        """
        if not citations:
            return

        sql = text("""
            INSERT INTO message_citations
                (id, message_id, tenant_id, space_id,
                 document_id, chunk_id, rank, score,
                 retrieval_mode, content_snapshot, title_snapshot)
            VALUES
                (:id, :message_id, :tenant_id, :space_id,
                 :document_id, :chunk_id, :rank, :score,
                 :retrieval_mode, :content_snapshot, :title_snapshot)
        """)

        rows = [
            {
                "id": str(uuid.uuid4()),
                "message_id": message_id,
                "tenant_id": tenant_id,
                "space_id": _valid_uuid(space_id),
                "document_id": _valid_uuid(c.get("doc_id")),
                "chunk_id": _valid_uuid(c.get("chunk_id")),
                "rank": idx + 1,
                "score": c.get("score"),
                "retrieval_mode": c.get("source", "unknown"),
                "content_snapshot": c.get("content", ""),
                "title_snapshot": c.get("doc_name"),
            }
            for idx, c in enumerate(citations)
        ]

        async with get_db() as db:
            await db.execute(sql, rows)

    async def get_by_message_ids(
        self, message_ids: list[str], tenant_id: str
    ) -> dict[str, list[dict]]:
        """批量查询多条消息的 citations，返回 {message_id: [citation, ...]}。"""
        if not message_ids:
            return {}
        sql = text("""
            SELECT message_id, document_id, chunk_id, rank, score,
                   retrieval_mode, content_snapshot, title_snapshot
            FROM message_citations
            WHERE message_id = ANY(:message_ids)
              AND tenant_id = :tenant_id
            ORDER BY message_id, rank
        """)
        async with get_db() as db:
            result = await db.execute(
                sql,
                {
                    "message_ids": message_ids,
                    "tenant_id": tenant_id,
                },
            )
            rows = result.fetchall()

        grouped: dict[str, list[dict]] = {}
        for row in rows:
            r = dict(row._mapping)
            mid = str(r["message_id"])
            grouped.setdefault(mid, []).append(
                {
                    "doc_id": str(r["document_id"]) if r["document_id"] else None,
                    "chunk_id": str(r["chunk_id"]) if r["chunk_id"] else None,
                    "doc_name": r["title_snapshot"],
                    "content": r["content_snapshot"],
                    "score": r["score"],
                    "source": r["retrieval_mode"],
                    "rank": r["rank"],
                }
            )
        return grouped
