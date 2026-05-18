from __future__ import annotations

import uuid

from sqlalchemy import text

from app.domain.memory import Message, Session
from app.infrastructure.postgres.client import get_session as get_db


class SessionRepository:
    """sessions + messages 表的持久化操作"""

    # ── Session CRUD ─────────────────────────────────────────────────────────

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        title: str | None = None,
        space_id: str | None = None,
    ) -> Session:
        session_id = str(uuid.uuid4())
        sql = text("""
            INSERT INTO sessions (session_id, tenant_id, user_id, title, space_id)
            VALUES (:session_id, :tenant_id, :user_id, :title, :space_id)
        """)
        async with get_db() as db:
            await db.execute(sql, {
                "session_id": session_id,
                "tenant_id":  tenant_id,
                "user_id":    user_id,
                "title":      title,
                "space_id":   space_id,
            })
        return Session(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            space_id=space_id,
            title=title,
        )

    async def get_by_id(
        self, session_id: str, tenant_id: str, user_id: str
    ) -> Session | None:
        sql = text("""
            SELECT session_id, tenant_id, user_id, title, summary,
                   message_count, created_at, updated_at
            FROM sessions
            WHERE session_id = :session_id
              AND tenant_id  = :tenant_id
              AND user_id    = :user_id
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "session_id": session_id, "tenant_id": tenant_id, "user_id": user_id
            })
            row = result.one_or_none()
        return _row_to_session(row) if row else None

    async def list(
        self, tenant_id: str, user_id: str, limit: int = 20, offset: int = 0, space_id: str | None = None,
    ) -> list[Session]:
        where_extra = "AND space_id = :space_id" if space_id else ""
        sql = text(f"""
            SELECT session_id, tenant_id, user_id, title, summary,
                message_count, created_at, updated_at
            FROM sessions
            WHERE tenant_id = :tenant_id AND user_id = :user_id  {where_extra}
            ORDER BY updated_at DESC
            LIMIT :limit OFFSET :offset
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "tenant_id": tenant_id, "user_id": user_id,
                "limit": limit, "offset": offset,"space_id":space_id,
            })
            rows = result.fetchall()
        return [_row_to_session(r) for r in rows]

    async def delete(self, session_id: str, tenant_id: str, user_id: str) -> bool:
        sql = text("""
            DELETE FROM sessions
            WHERE session_id = :session_id
            AND tenant_id  = :tenant_id
            AND user_id    = :user_id
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "session_id": session_id, "tenant_id": tenant_id, "user_id": user_id
            })
            return result.rowcount > 0

    async def update_summary(self, session_id: str, summary: str) -> None:
        sql = text("""
            UPDATE sessions
            SET summary = :summary, updated_at = NOW()
            WHERE session_id = :session_id
        """)
        async with get_db() as db:
            await db.execute(sql, {"session_id": session_id, "summary": summary})

    # ── Message CRUD ──────────────────────────────────────────────────────────

    async def add_message(
        self,
        session_id: str,
        tenant_id: str,
        user_id: str,
        role: str,
        content: str,
        token_count: int = 0,
    ) -> Message:
        message_id = str(uuid.uuid4())
        insert_sql = text("""
            INSERT INTO messages
                (message_id, session_id, tenant_id, user_id, role, content, token_count)
            VALUES
                (:message_id, :session_id, :tenant_id, :user_id, :role, :content, :token_count)
        """)
        inc_sql = text("""
            UPDATE sessions
            SET message_count = message_count + 1, updated_at = NOW()
            WHERE session_id = :session_id
        """)
        async with get_db() as db:
            await db.execute(insert_sql, {
                "message_id":  message_id,
                "session_id":  session_id,
                "tenant_id":   tenant_id,
                "user_id":     user_id,
                "role":        role,
                "content":     content,
                "token_count": token_count,
            })
            await db.execute(inc_sql, {"session_id": session_id})
        return Message(
            message_id=message_id, session_id=session_id,
            tenant_id=tenant_id, user_id=user_id,
            role=role, content=content, token_count=token_count,
        )

    async def get_all_messages(
        self, session_id: str, tenant_id: str, user_id: str
    ) -> list[Message]:
        """按时间正序返回全部消息"""
        sql = text("""
            SELECT message_id, session_id, tenant_id, user_id,
                   role, content, token_count, created_at
            FROM messages
            WHERE session_id = :session_id
              AND tenant_id  = :tenant_id
            ORDER BY created_at ASC
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "session_id": session_id, "tenant_id": tenant_id,
            })
            rows = result.fetchall()
        return [_row_to_message(r) for r in rows]

    async def get_first_messages(
        self, session_id: str, tenant_id: str, user_id: str, n: int
    ) -> list[Message]:
        """返回最早的 n 条消息（时间正序）"""
        sql = text("""
            SELECT message_id, session_id, tenant_id, user_id,
                   role, content, token_count, created_at
            FROM messages
            WHERE session_id = :session_id
              AND tenant_id  = :tenant_id
            ORDER BY created_at ASC
            LIMIT :n
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "session_id": session_id, "tenant_id": tenant_id, "n": n,
            })
            rows = result.fetchall()
        return [_row_to_message(r) for r in rows]

    async def get_last_messages(
        self, session_id: str, tenant_id: str, user_id: str, n: int
    ) -> list[Message]:
        """返回最近的 n 条消息（时间正序）"""
        sql = text("""
            SELECT message_id, session_id, tenant_id, user_id,
                   role, content, token_count, created_at
            FROM messages
            WHERE session_id = :session_id
              AND tenant_id  = :tenant_id
            ORDER BY created_at DESC
            LIMIT :n
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "session_id": session_id, "tenant_id": tenant_id, "n": n,
            })
            rows = result.fetchall()
        # DESC 取出后反转为正序
        return [_row_to_message(r) for r in reversed(rows)]

    async def get_middle_messages(
        self,
        session_id: str,
        tenant_id: str,
        user_id: str,
        skip_first: int,
        skip_last: int,
    ) -> list[Message]:
        """返回跳过最早 skip_first 和最新 skip_last 后的中间段消息（用于压缩）"""
        sql = text("""
            SELECT message_id, session_id, tenant_id, user_id,
                   role, content, token_count, created_at
            FROM messages
            WHERE session_id = :session_id
              AND tenant_id  = :tenant_id
            ORDER BY created_at ASC
            OFFSET :skip_first
        """)
        async with get_db() as db:
            result = await db.execute(sql, {
                "session_id":  session_id,
                "tenant_id":   tenant_id,
                "skip_first":  skip_first,
            })
            rows = result.fetchall()
        messages = [_row_to_message(r) for r in rows]
        if skip_last > 0:
            messages = messages[:-skip_last]
        return messages


# ── helpers ───────────────────────────────────────────────────────────────────

def _row_to_session(row) -> Session:
    r = dict(row._mapping)
    return Session(
        session_id=str(r["session_id"]),
        tenant_id=r["tenant_id"],
        user_id=r["user_id"],
        space_id=str(r["space_id"]) if r.get("space_id") else None,
        title=r.get("title"),
        summary=r.get("summary"),
        message_count=r.get("message_count", 0),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _row_to_message(row) -> Message:
    r = dict(row._mapping)
    return Message(
        message_id=str(r["message_id"]),
        session_id=str(r["session_id"]),
        tenant_id=r["tenant_id"],
        user_id=r["user_id"],
        role=r["role"],
        content=r["content"],
        token_count=r.get("token_count", 0),
        created_at=r["created_at"],
    )
