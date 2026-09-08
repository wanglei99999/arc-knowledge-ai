from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text

from app.infrastructure.postgres.client import get_session


def row_dict(row) -> dict:
    return {
        key: str(value) if isinstance(value, UUID) else value for key, value in row._mapping.items()
    }


# Both single-object checks and listings use exactly the same grant predicate.
# UNION deduplicates ancestors even if the user belongs to multiple descendant groups.
ACCESS_CTE = """
WITH RECURSIVE active_member AS (
    SELECT m.id FROM tenant_memberships m
    JOIN tenants t ON t.id=m.tenant_id AND t.status='active'
    JOIN users u ON u.id=m.user_id AND u.is_active
    WHERE m.tenant_id=:tenant_id AND m.id=:membership_id AND m.user_id=:user_id
      AND m.status='active'
), member_groups AS (
    SELECT g.id, g.parent_group_id FROM groups g
    JOIN group_memberships gm ON gm.tenant_id=g.tenant_id AND gm.group_id=g.id
    WHERE gm.tenant_id=:tenant_id AND gm.tenant_membership_id IN (SELECT id FROM active_member)
      AND g.status='active'
    UNION
    SELECT p.id, p.parent_group_id FROM groups p
    JOIN member_groups c ON p.id=c.parent_group_id
    WHERE p.tenant_id=:tenant_id AND p.status='active'
), matched_grants AS (
    SELECT sg.* FROM space_grants sg
    WHERE sg.tenant_id=:tenant_id AND sg.revoked_at IS NULL
      AND EXISTS (SELECT 1 FROM active_member)
      AND (sg.subject_type='TENANT'
        OR (sg.subject_type='MEMBER' AND sg.tenant_membership_id=:membership_id)
        OR (sg.subject_type='GROUP' AND sg.group_id IN (SELECT id FROM member_groups)))
)
"""


class AuthorizationRepository:
    async def resolve_context(self, tenant_id, user_id, membership_id=None):
        async with get_session() as db:
            result = await db.execute(
                text("""
                SELECT m.tenant_id, m.user_id, m.id AS membership_id,
                       m.role AS tenant_role, u.email, t.name AS tenant_name
                FROM tenant_memberships m JOIN users u ON u.id=m.user_id
                JOIN tenants t ON t.id=m.tenant_id
                WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                  AND m.status='active' AND t.status='active' AND u.is_active
            """),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
            row = result.one_or_none()
            if row is None:
                return None
            context = row_dict(row)
            if membership_id is not None and context["membership_id"] != membership_id:
                return None
            return context

    async def space_access(self, ctx, space_id=None, include_archived=False):
        conditions = []
        params = {
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
            "membership_id": ctx.membership_id,
        }
        if space_id is not None:
            try:
                UUID(space_id)
            except (ValueError, TypeError):
                return []
            conditions.append("s.id=:space_id")
            params["space_id"] = space_id
        if not include_archived:
            conditions.append("s.status='active'")
        else:
            conditions.append("s.status IN ('active','archived')")
        sql = (
            ACCESS_CTE
            + """
            SELECT s.id AS space_id,s.space_key,s.name,s.status,s.created_by,
              jsonb_agg(jsonb_build_object('id',g.id,'subject_type',g.subject_type,
                'group_id',g.group_id,'tenant_membership_id',g.tenant_membership_id,
                'role',g.role,'source',g.source)) AS permission_sources,
              CASE WHEN EXISTS (SELECT 1 FROM space_grants v WHERE v.tenant_id=s.tenant_id
                 AND v.space_id=s.id AND v.revoked_at IS NULL AND v.subject_type='TENANT')
                THEN 'public'
                WHEN EXISTS (SELECT 1 FROM space_grants v JOIN groups vg
                  ON vg.id=v.group_id AND vg.tenant_id=v.tenant_id AND vg.status='active'
                  WHERE v.tenant_id=s.tenant_id AND v.space_id=s.id AND v.revoked_at IS NULL)
                THEN 'team' ELSE 'private' END AS visibility_summary
            FROM spaces s JOIN matched_grants g ON g.space_id=s.id AND g.tenant_id=s.tenant_id
            WHERE s.tenant_id=:tenant_id AND """
            + " AND ".join(conditions)
            + """
            GROUP BY s.id ORDER BY s.created_at,s.id
        """
        )
        async with get_session() as db:
            rows = (await db.execute(text(sql), params)).all()
            output = [row_dict(row) for row in rows]
            for item in output:
                if isinstance(item["permission_sources"], str):
                    item["permission_sources"] = json.loads(item["permission_sources"])
            return output

    async def document_scope(self, tenant_id, document_id):
        try:
            UUID(document_id)
        except (ValueError, TypeError):
            return None
        async with get_session() as db:
            row = (
                await db.execute(
                    text("""
                SELECT id,space_id,created_by_membership_id FROM documents
                WHERE tenant_id=:tenant_id AND id=:document_id
                  AND deleted_at IS NULL AND status NOT IN ('deleted','deleting')
            """),
                    {"tenant_id": tenant_id, "document_id": document_id},
                )
            ).one_or_none()
            return row_dict(row) if row else None

    async def audit(self, ctx, action, resource_type, resource_id, metadata=None):
        async with get_session() as db:
            await write_audit(db, ctx, action, resource_type, resource_id, metadata)

    async def session_scope(self, tenant_id, user_id, session_id=None, turn_id=None):
        value = turn_id if turn_id is not None else session_id
        try:
            UUID(str(value))
        except (ValueError, TypeError):
            return None
        condition = (
            "s.session_id=:id"
            if turn_id is None
            else "EXISTS (SELECT 1 FROM messages m WHERE m.message_id=:id AND m.session_id=s.session_id AND m.tenant_id=s.tenant_id)"
        )
        async with get_session() as db:
            row = (
                await db.execute(
                    text("""
                SELECT s.session_id,s.space_id,s.archived_at FROM sessions s
                WHERE s.tenant_id=:tenant_id AND s.user_id=:user_id AND """ + condition),
                    {"tenant_id": tenant_id, "user_id": user_id, "id": value},
                )
            ).one_or_none()
            return row_dict(row) if row else None


async def write_audit(db, ctx, action, resource_type, resource_id, metadata=None):
    await db.execute(
        text("""
        INSERT INTO audit_events (tenant_id,actor_membership_id,action,resource_type,resource_id,metadata)
        VALUES (:tenant_id,:actor,:action,:type,:id,CAST(:metadata AS jsonb))
    """),
        {
            "tenant_id": ctx.tenant_id,
            "actor": ctx.membership_id,
            "action": action,
            "type": resource_type,
            "id": str(resource_id),
            "metadata": json.dumps(metadata or {}),
        },
    )
