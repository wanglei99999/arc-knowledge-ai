"""Transactional organization and grant administration.

All mutations lock the tenant before checking authority and changing relationships.
This serializes last-admin/manager checks with membership, group and grant changes.
"""

from uuid import uuid4

from sqlalchemy import text

from app.domain.authorization import AuthorizationError
from app.infrastructure.postgres.client import get_session
from app.infrastructure.postgres.repositories.authorization_repo import (
    ACCESS_CTE,
    row_dict,
    write_audit,
)
from app.services.authorization_service import authorization


def fail(status, code, message):
    raise AuthorizationError(status, code, message)


async def rows(db, sql, params):
    return [row_dict(r) for r in (await db.execute(text(sql), params)).all()]


class AccessManagementService:
    async def _lock(self, db, ctx, admin=False):
        await db.execute(
            text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), {"tenant": ctx.tenant_id}
        )
        records = await rows(
            db,
            """SELECT m.role FROM tenant_memberships m
            JOIN users u ON u.id=m.user_id AND u.is_active
            JOIN tenants t ON t.id=m.tenant_id AND t.status='active'
            WHERE m.tenant_id=:tenant AND m.id=:member AND m.user_id=:user AND m.status='active'""",
            {"tenant": ctx.tenant_id, "member": ctx.membership_id, "user": ctx.user_id},
        )
        if not records:
            fail(403, "TENANT_CONTEXT_INVALID", "当前组织成员身份已失效")
        if admin and records[0]["role"] != "TENANT_ADMIN":
            fail(403, "PERMISSION_DENIED", "需要租户管理员权限")
        return records[0]["role"] == "TENANT_ADMIN"

    async def _space(self, db, ctx, space_id, *, control=False):
        data = await rows(
            db,
            "SELECT * FROM spaces WHERE tenant_id=:tenant AND id=:id FOR UPDATE",
            {"tenant": ctx.tenant_id, "id": space_id},
        )
        if not data:
            fail(404, "RESOURCE_NOT_FOUND", "空间不存在")
        if not control:
            grants = await rows(
                db,
                ACCESS_CTE + "SELECT role FROM matched_grants WHERE space_id=:space_id",
                {
                    "tenant_id": ctx.tenant_id,
                    "user_id": ctx.user_id,
                    "membership_id": ctx.membership_id,
                    "space_id": space_id,
                },
            )
            if not grants:
                fail(404, "RESOURCE_NOT_FOUND", "空间不存在或无权访问")
            if not any(g["role"] == "MANAGER" for g in grants):
                fail(403, "PERMISSION_DENIED", "需要空间管理权限")
        return data[0]

    async def _active_subject(self, db, ctx, subject_type, group_id=None, membership_id=None):
        if subject_type == "TENANT":
            if group_id or membership_id:
                fail(422, "INVALID_SUBJECT", "租户授权不应指定成员或群组")
            return
        if subject_type == "GROUP" and group_id and not membership_id:
            table, value = "groups", group_id
        elif subject_type == "MEMBER" and membership_id and not group_id:
            table, value = "tenant_memberships", membership_id
        else:
            fail(422, "INVALID_SUBJECT", "授权主体字段不匹配")
        exists = await rows(
            db,
            f"SELECT id FROM {table} WHERE tenant_id=:tenant AND id=:id AND status='active'",
            {"tenant": ctx.tenant_id, "id": value},
        )
        if not exists:
            fail(404, "RESOURCE_NOT_FOUND", "授权对象不存在或已停用")

    async def _write_grant(
        self,
        db,
        ctx,
        space_id,
        subject_type,
        role,
        group_id=None,
        membership_id=None,
        source="direct",
    ):
        await self._active_subject(db, ctx, subject_type, group_id, membership_id)
        params = {
            "tenant": ctx.tenant_id,
            "space": space_id,
            "subject": subject_type,
            "role": role,
            "group": group_id,
            "member": membership_id,
            "actor": ctx.membership_id,
            "source": source,
        }
        # Preserve the previous role in history; partial unique indexes enforce one active grant.
        await db.execute(
            text("""UPDATE space_grants SET revoked_at=NOW()
            WHERE tenant_id=:tenant AND space_id=:space AND subject_type=:subject
              AND group_id IS NOT DISTINCT FROM CAST(:group AS uuid)
              AND tenant_membership_id IS NOT DISTINCT FROM CAST(:member AS uuid)
              AND revoked_at IS NULL"""),
            params,
        )
        grant = (
            await rows(
                db,
                """INSERT INTO space_grants(tenant_id,space_id,subject_type,
            role,group_id,tenant_membership_id,created_by_membership_id,source)
            VALUES (:tenant,:space,:subject,:role,:group,:member,:actor,:source) RETURNING *""",
                params,
            )
        )[0]
        await write_audit(
            db,
            ctx,
            "space.grant",
            "space",
            space_id,
            {
                "grant_id": grant["id"],
                "subject_type": subject_type,
                "group_id": group_id,
                "tenant_membership_id": membership_id,
                "role": role,
                "source": source,
            },
        )
        return grant

    async def _has_manager(self, db, ctx, space_id):
        return bool(
            await rows(
                db,
                """
            WITH RECURSIVE eligible_groups AS (
                SELECT g.id,g.parent_group_id FROM groups g
                JOIN group_memberships gm ON gm.group_id=g.id AND gm.tenant_id=g.tenant_id
                JOIN tenant_memberships m ON m.id=gm.tenant_membership_id AND m.tenant_id=gm.tenant_id
                JOIN users u ON u.id=m.user_id AND u.is_active
                WHERE g.tenant_id=:tenant AND g.status='active' AND m.status='active'
                UNION
                SELECT g.id,g.parent_group_id FROM groups g JOIN eligible_groups c ON c.parent_group_id=g.id
                WHERE g.tenant_id=:tenant AND g.status='active'
            )
            SELECT 1 FROM space_grants sg WHERE sg.tenant_id=:tenant AND sg.space_id=:space
              AND sg.revoked_at IS NULL AND sg.role='MANAGER' AND (
                sg.subject_type='TENANT' OR sg.group_id IN (SELECT id FROM eligible_groups)
                OR sg.tenant_membership_id IN (SELECT m.id FROM tenant_memberships m
                    JOIN users u ON u.id=m.user_id AND u.is_active
                    WHERE m.tenant_id=:tenant AND m.status='active')) LIMIT 1
        """,
                {"tenant": ctx.tenant_id, "space": space_id},
            )
        )

    async def create_space(self, ctx, name, preset="private", group_id=None, group_role="EDITOR"):
        async with get_session() as db:
            await self._lock(db, ctx, admin=preset == "public")
            space_id = str(uuid4())
            await db.execute(
                text(
                    """INSERT INTO spaces(id,tenant_id,space_key,name,created_by,created_by_membership_id)
                VALUES (:id,:tenant,:key,:name,:user,:member)"""
                ),
                {
                    "id": space_id,
                    "tenant": ctx.tenant_id,
                    "key": "space-" + space_id,
                    "name": name,
                    "user": ctx.user_id,
                    "member": ctx.membership_id,
                },
            )
            await self._write_grant(
                db,
                ctx,
                space_id,
                "MEMBER",
                "MANAGER",
                membership_id=ctx.membership_id,
                source="template",
            )
            if preset == "public":
                await self._write_grant(db, ctx, space_id, "TENANT", "VIEWER", source="template")
            elif preset == "group":
                await self._write_grant(
                    db, ctx, space_id, "GROUP", group_role, group_id=group_id, source="template"
                )
            await write_audit(db, ctx, "space.create", "space", space_id, {"preset": preset})
        return await authorization.authorize_user(
            ctx.tenant_id, ctx.user_id, space_id, "space.read"
        )

    async def update_space(self, ctx, space_id, *, name=None, status=None):
        async with get_session() as db:
            is_admin = await self._lock(db, ctx)
            await self._space(db, ctx, space_id, control=is_admin and status is not None)
            await db.execute(
                text("""UPDATE spaces SET name=COALESCE(:name,name),status=COALESCE(:status,status),
                updated_at=NOW() WHERE tenant_id=:tenant AND id=:id"""),
                {"tenant": ctx.tenant_id, "id": space_id, "name": name, "status": status},
            )
            await write_audit(db, ctx, "space." + (status or "rename"), "space", space_id)

    async def grants(self, ctx, space_id):
        async with get_session() as db:
            admin = await self._lock(db, ctx)
            await self._space(db, ctx, space_id, control=admin)
            return await rows(
                db,
                "SELECT * FROM space_grants WHERE tenant_id=:tenant AND space_id=:id AND revoked_at IS NULL ORDER BY created_at",
                {"tenant": ctx.tenant_id, "id": space_id},
            )

    async def put_grant(
        self, ctx, space_id, subject_type, role, group_id=None, tenant_membership_id=None
    ):
        async with get_session() as db:
            await self._lock(db, ctx, admin=subject_type == "TENANT")
            await self._space(db, ctx, space_id)
            grant = await self._write_grant(
                db, ctx, space_id, subject_type, role, group_id, tenant_membership_id
            )
            if not await self._has_manager(db, ctx, space_id):
                fail(409, "LAST_SPACE_MANAGER", "空间必须保留至少一位有效管理者")
            return grant

    async def revoke_grant(self, ctx, space_id, grant_id):
        async with get_session() as db:
            is_admin = await self._lock(db, ctx)
            await self._space(db, ctx, space_id)
            data = await rows(
                db,
                "SELECT * FROM space_grants WHERE tenant_id=:tenant AND space_id=:space AND id=:id AND revoked_at IS NULL",
                {"tenant": ctx.tenant_id, "space": space_id, "id": grant_id},
            )
            if not data:
                fail(404, "RESOURCE_NOT_FOUND", "授权不存在")
            if data[0]["subject_type"] == "TENANT" and not is_admin:
                fail(403, "PERMISSION_DENIED", "租户全体授权需要租户管理员权限")
            await db.execute(
                text("UPDATE space_grants SET revoked_at=NOW() WHERE id=:id"), {"id": grant_id}
            )
            if not await self._has_manager(db, ctx, space_id):
                fail(409, "LAST_SPACE_MANAGER", "空间必须保留至少一位有效管理者")
            await write_audit(
                db, ctx, "space.grant.revoke", "space", space_id, {"grant_id": grant_id}
            )

    async def takeover(self, ctx, space_id, reason):
        if not reason.strip():
            fail(422, "TAKEOVER_REASON_REQUIRED", "请填写接管原因")
        async with get_session() as db:
            await self._lock(db, ctx, admin=True)
            await self._space(db, ctx, space_id, control=True)
            await self._write_grant(
                db,
                ctx,
                space_id,
                "MEMBER",
                "MANAGER",
                membership_id=ctx.membership_id,
                source="admin_takeover",
            )
            await write_audit(
                db, ctx, "space.takeover", "space", space_id, {"reason": reason.strip()}
            )

    async def directory(self, ctx, admin=False):
        async with get_session() as db:
            await self._lock(db, ctx, admin=admin)
            members = await rows(
                db,
                """SELECT m.id AS membership_id,m.user_id,u.email,m.role,m.status
                FROM tenant_memberships m JOIN users u ON u.id=m.user_id
                WHERE m.tenant_id=:tenant """
                + ("" if admin else "AND m.status='active' AND u.is_active ")
                + "ORDER BY u.email",
                {"tenant": ctx.tenant_id},
            )
            groups = await rows(
                db,
                "SELECT id,name,parent_group_id,status FROM groups WHERE tenant_id=:tenant AND status='active' ORDER BY name",
                {"tenant": ctx.tenant_id},
            )
            return {
                "members": (
                    members
                    if admin
                    else [
                        {"membership_id": m["membership_id"], "email": m["email"]} for m in members
                    ]
                ),
                "groups": groups,
            }

    async def add_member(self, ctx, email, role):
        async with get_session() as db:
            await self._lock(db, ctx, admin=True)
            users = await rows(
                db,
                "SELECT id FROM users WHERE email=:email AND is_active",
                {"email": email.strip().lower()},
            )
            if not users:
                fail(404, "RESOURCE_NOT_FOUND", "该用户尚未注册或已停用")
            existing = await rows(
                db,
                "SELECT id FROM tenant_memberships WHERE tenant_id=:tenant AND user_id=:user",
                {"tenant": ctx.tenant_id, "user": users[0]["id"]},
            )
            if existing:
                fail(409, "MEMBERSHIP_EXISTS", "成员身份已存在，请更新其状态")
            member = (
                await rows(
                    db,
                    """INSERT INTO tenant_memberships(tenant_id,user_id,role)
                VALUES (:tenant,:user,:role) RETURNING id AS membership_id,user_id,role,status""",
                    {"tenant": ctx.tenant_id, "user": users[0]["id"], "role": role},
                )
            )[0]
            await write_audit(db, ctx, "member.add", "member", member["membership_id"])
            # Every new member receives one private space owned by their membership.
            sid = str(uuid4())
            await db.execute(
                text(
                    """INSERT INTO spaces(id,tenant_id,space_key,name,created_by,created_by_membership_id)
                VALUES (:id,:tenant,:key,'我的空间',:user,:member)"""
                ),
                {
                    "id": sid,
                    "tenant": ctx.tenant_id,
                    "key": "personal-" + member["membership_id"],
                    "user": users[0]["id"],
                    "member": member["membership_id"],
                },
            )
            await self._write_grant(
                db,
                ctx,
                sid,
                "MEMBER",
                "MANAGER",
                membership_id=member["membership_id"],
                source="template",
            )
            return {**member, "email": email.strip().lower()}

    async def update_member(self, ctx, membership_id, role=None, status=None):
        async with get_session() as db:
            await self._lock(db, ctx, admin=True)
            updated = await rows(
                db,
                """UPDATE tenant_memberships SET role=COALESCE(:role,role),status=COALESCE(:status,status)
                WHERE tenant_id=:tenant AND id=:id RETURNING id AS membership_id,user_id,role,status""",
                {"tenant": ctx.tenant_id, "id": membership_id, "role": role, "status": status},
            )
            if not updated:
                fail(404, "RESOURCE_NOT_FOUND", "成员不存在")
            admins = await rows(
                db,
                """SELECT m.id FROM tenant_memberships m JOIN users u ON u.id=m.user_id AND u.is_active
                WHERE m.tenant_id=:tenant AND m.role='TENANT_ADMIN' AND m.status='active' LIMIT 1""",
                {"tenant": ctx.tenant_id},
            )
            if not admins:
                fail(409, "LAST_TENANT_ADMIN", "必须保留至少一位有效租户管理员")
            await write_audit(
                db, ctx, "member.update", "member", membership_id, {"role": role, "status": status}
            )
            email = await rows(
                db, "SELECT email FROM users WHERE id=:id", {"id": updated[0]["user_id"]}
            )
            return {**updated[0], "email": email[0]["email"]}

    async def save_group(
        self, ctx, name=None, parent_group_id=None, group_id=None, change_parent=True, archive=False
    ):
        async with get_session() as db:
            await self._lock(db, ctx, admin=True)
            if group_id:
                await self._active_subject(db, ctx, "GROUP", group_id)
            if parent_group_id:
                await self._active_subject(db, ctx, "GROUP", parent_group_id)
            if group_id and change_parent and parent_group_id:
                ancestors = await rows(
                    db,
                    """WITH RECURSIVE ancestors AS (
                    SELECT id,parent_group_id FROM groups WHERE tenant_id=:tenant AND id=:parent
                    UNION SELECT g.id,g.parent_group_id FROM groups g JOIN ancestors a ON a.parent_group_id=g.id
                    WHERE g.tenant_id=:tenant) SELECT id FROM ancestors WHERE id=:id""",
                    {"tenant": ctx.tenant_id, "parent": parent_group_id, "id": group_id},
                )
                if ancestors:
                    fail(409, "GROUP_CYCLE", "群组层级不能形成环路")
            if archive:
                children = await rows(
                    db,
                    "SELECT id FROM groups WHERE tenant_id=:tenant AND parent_group_id=:id AND status='active'",
                    {"tenant": ctx.tenant_id, "id": group_id},
                )
                if children:
                    fail(409, "GROUP_HAS_CHILDREN", "请先处理子群组")
            params = {
                "tenant": ctx.tenant_id,
                "id": group_id or str(uuid4()),
                "name": name,
                "parent": parent_group_id,
                "actor": ctx.membership_id,
                "status": "archived" if archive else "active",
            }
            if group_id:
                parent_sql = ",parent_group_id=:parent" if change_parent else ""
                result = await rows(
                    db,
                    "UPDATE groups SET name=COALESCE(:name,name),status=:status"
                    + parent_sql
                    + " WHERE tenant_id=:tenant AND id=:id RETURNING id,name,parent_group_id,status",
                    params,
                )
            else:
                result = await rows(
                    db,
                    """INSERT INTO groups(id,tenant_id,name,parent_group_id,created_by_membership_id)
                    VALUES (:id,:tenant,:name,:parent,:actor) RETURNING id,name,parent_group_id,status""",
                    params,
                )
            await write_audit(
                db,
                ctx,
                "group.archive" if archive else "group.save",
                "group",
                params["id"],
                {"parent_group_id": parent_group_id},
            )
            return result[0]

    async def group_members(self, ctx, group_id, *, membership_id=None, remove=False):
        async with get_session() as db:
            await self._lock(db, ctx, admin=True)
            await self._active_subject(db, ctx, "GROUP", group_id)
            params = {
                "tenant": ctx.tenant_id,
                "group": group_id,
                "member": membership_id,
                "actor": ctx.membership_id,
            }
            if membership_id:
                if remove:
                    await db.execute(
                        text(
                            "DELETE FROM group_memberships WHERE tenant_id=:tenant AND group_id=:group AND tenant_membership_id=:member"
                        ),
                        params,
                    )
                else:
                    await self._active_subject(db, ctx, "MEMBER", membership_id=membership_id)
                    await db.execute(
                        text(
                            """INSERT INTO group_memberships(tenant_id,group_id,tenant_membership_id,created_by_membership_id)
                        VALUES (:tenant,:group,:member,:actor) ON CONFLICT DO NOTHING"""
                        ),
                        params,
                    )
                await write_audit(
                    db,
                    ctx,
                    "group.member.remove" if remove else "group.member.add",
                    "group",
                    group_id,
                    {"membership_id": membership_id},
                )
            return await rows(
                db,
                """SELECT m.id AS membership_id,u.email FROM group_memberships gm
                JOIN tenant_memberships m ON m.id=gm.tenant_membership_id AND m.tenant_id=gm.tenant_id
                JOIN users u ON u.id=m.user_id WHERE gm.tenant_id=:tenant AND gm.group_id=:group ORDER BY u.email""",
                params,
            )

    async def tenant_spaces(self, ctx):
        async with get_session() as db:
            await self._lock(db, ctx, admin=True)
            return await rows(
                db,
                "SELECT id AS space_id,space_key,name,status,created_by FROM spaces WHERE tenant_id=:tenant ORDER BY created_at",
                {"tenant": ctx.tenant_id},
            )

    async def audit_events(self, ctx):
        async with get_session() as db:
            await self._lock(db, ctx, admin=True)
            return await rows(
                db,
                "SELECT * FROM audit_events WHERE tenant_id=:tenant ORDER BY created_at DESC LIMIT 200",
                {"tenant": ctx.tenant_id},
            )
