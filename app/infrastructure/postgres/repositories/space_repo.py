from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from app.domain.space import Space
from app.infrastructure.postgres.session import get_session


def _slugify(name: str) -> str:
    """将名称转为 URL 友好的 space_key"""
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = slug.strip('-')
    return slug or 'space'


def _row_to_space(row: object) -> Space:
    m = row._mapping  # type: ignore[attr-defined]
    return Space(
        space_id=str(m['id']),
        tenant_id=m['tenant_id'],
        space_key=m['space_key'],
        name=m['name'] or '',
        status=m['status'],
        created_by=m.get('created_by'),
        created_at=m['created_at'] if isinstance(m['created_at'], datetime)
            else datetime.now(timezone.utc),
    )


class SpaceRepository:

    async def list_by_tenant(self, tenant_id: str) -> list[Space]:
        sql = text("""
            SELECT id, tenant_id, space_key, name, status, created_by, created_at
            FROM spaces
            WHERE tenant_id = :tenant_id AND status = 'active'
            ORDER BY created_at ASC
        """)
        async with get_session() as db:
            result = await db.execute(sql, {'tenant_id': tenant_id})
            return [_row_to_space(r) for r in result.fetchall()]

    async def find_by_key(self, tenant_id: str, space_key: str) -> Space | None:
        sql = text("""
            SELECT id, tenant_id, space_key, name, status, created_by, created_at
            FROM spaces
            WHERE tenant_id = :tenant_id AND space_key = :space_key
        """)
        async with get_session() as db:
            result = await db.execute(sql, {'tenant_id': tenant_id, 'space_key': space_key})
            row = result.one_or_none()
            return _row_to_space(row) if row else None

    async def create(
        self,
        tenant_id: str,
        name: str,
        created_by: str | None = None,
    ) -> Space:
        base_key = _slugify(name)
        space_key = base_key
        async with get_session() as db:
            for i in range(2, 100):
                check = text("""
                    SELECT 1 FROM spaces
                    WHERE tenant_id = :tenant_id AND space_key = :space_key
                """)
                r = await db.execute(check, {'tenant_id': tenant_id, 'space_key': space_key})
                if r.one_or_none() is None:
                    break
                space_key = f'{base_key}-{i}'

            space_id = str(uuid.uuid4())
            sql = text("""
                INSERT INTO spaces (id, tenant_id, space_key, name, status, created_by)
                VALUES (:id, :tenant_id, :space_key, :name, 'active', :created_by)
                RETURNING id, tenant_id, space_key, name, status, created_by, created_at
            """)
            result = await db.execute(sql, {
                'id': space_id,
                'tenant_id': tenant_id,
                'space_key': space_key,
                'name': name,
                'created_by': created_by,
            })
            await db.commit()
            return _row_to_space(result.one())

    async def ensure_default(self, tenant_id: str) -> Space:
        """幂等：保证默认空间存在"""
        existing = await self.find_by_key(tenant_id, 'default')
        if existing:
            return existing
        return await self.create(tenant_id, '默认空间')
