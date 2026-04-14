from __future__ import annotations

import uuid

from sqlalchemy import text

from app.domain.memory import Memory, MemoryCategory
from app.infrastructure.postgres.client import get_session as get_db


class MemoryRepository:
    """memories 表的持久化操作"""

    async def save(self, memory: Memory) -> None:
        sql = text("""
            INSERT INTO memories
                (memory_id, tenant_id, user_id, category, content,
                 source_session_id, confidence)
            VALUES
                (:memory_id, :tenant_id, :user_id, :category, :content,
                 :source_session_id, :confidence)
        """)
        async with get_db() as db:
            await db.execute(sql, {
                "memory_id":         memory.memory_id,
                "tenant_id":         memory.tenant_id,
                "user_id":           memory.user_id,
                "category":          memory.category.value,
                "content":           memory.content,
                "source_session_id": memory.source_session_id,
                "confidence":        memory.confidence,
            })

    async def update_content(self, memory_id: str, content: str) -> None:
        sql = text("""
            UPDATE memories
            SET content = :content, updated_at = NOW()
            WHERE memory_id = :memory_id
        """)
        async with get_db() as db:
            await db.execute(sql, {"memory_id": memory_id, "content": content})

    async def delete(self, memory_id: str) -> None:
        sql = text("""
            DELETE FROM memories WHERE memory_id = :memory_id
        """)
        async with get_db() as db:
            await db.execute(sql, {"memory_id": memory_id})

    async def get_by_ids(
        self, memory_ids: list[str], tenant_id: str, user_id: str
    ) -> list[Memory]:
        if not memory_ids:
            return []
        sql = text("""
            SELECT memory_id, tenant_id, user_id, category, content,
                   source_session_id, confidence, created_at, updated_at
            FROM memories
            WHERE memory_id = ANY(:ids)
              AND tenant_id = :tenant_id
              AND user_id   = :user_id
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "ids": memory_ids, "tenant_id": tenant_id, "user_id": user_id
            })
            rows = result.fetchall()
        return [_row_to_memory(r) for r in rows]

    async def list_by_user(
        self, tenant_id: str, user_id: str, limit: int = 100
    ) -> list[Memory]:
        sql = text("""
            SELECT memory_id, tenant_id, user_id, category, content,
                   source_session_id, confidence, created_at, updated_at
            FROM memories
            WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY updated_at DESC
            LIMIT :limit
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "tenant_id": tenant_id, "user_id": user_id, "limit": limit
            })
            rows = result.fetchall()
        return [_row_to_memory(r) for r in rows]

    async def delete_by_session(self, session_id: str) -> None:
        """删除某 session 相关的所有记忆（session 删除时清理）"""
        sql = text("""
            DELETE FROM memories WHERE source_session_id = :session_id
        """)
        async with get_db() as db:
            await db.execute(sql, {"session_id": session_id})


def _row_to_memory(row) -> Memory:
    r = dict(row._mapping)
    return Memory(
        memory_id=r["memory_id"],
        tenant_id=r["tenant_id"],
        user_id=r["user_id"],
        category=MemoryCategory(r["category"]),
        content=r["content"],
        source_session_id=r.get("source_session_id"),
        confidence=r.get("confidence", 1.0),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )
