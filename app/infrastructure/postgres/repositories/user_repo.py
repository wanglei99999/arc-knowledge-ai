from uuid import uuid4

from sqlalchemy import text

from app.domain.authorization import UserContext
from app.domain.user import User
from app.infrastructure.postgres.client import get_session
from app.infrastructure.postgres.repositories.authorization_repo import row_dict, write_audit


class UserRepository:
    @staticmethod
    def _user(row):
        if row is None:
            return None
        data = row_dict(row)
        data["user_id"] = data.pop("id")
        return User(**data)

    async def find_by_email(self, email):
        async with get_session() as db:
            return self._user(
                (
                    await db.execute(
                        text("SELECT * FROM users WHERE email=:email"),
                        {"email": email.strip().lower()},
                    )
                ).one_or_none()
            )

    async def find_by_id(self, user_id):
        async with get_session() as db:
            return self._user(
                (
                    await db.execute(text("SELECT * FROM users WHERE id=:id"), {"id": user_id})
                ).one_or_none()
            )

    async def memberships(self, user_id):
        async with get_session() as db:
            rows = (
                await db.execute(
                    text("""
                SELECT t.id AS tenant_id,t.name AS tenant_name,m.id AS membership_id,m.role AS tenant_role
                FROM tenant_memberships m JOIN tenants t ON t.id=m.tenant_id
                JOIN users u ON u.id=m.user_id
                WHERE m.user_id=:user_id AND m.status='active' AND t.status='active' AND u.is_active
                ORDER BY t.created_at,t.id
            """),
                    {"user_id": user_id},
                )
            ).all()
            return [row_dict(r) for r in rows]

    async def register_organization(self, user, tenant_name):
        tenant_id, membership_id = str(uuid4()), str(uuid4())
        async with get_session() as db:
            await db.execute(
                text("INSERT INTO users(id,email,hashed_password) VALUES (:id,:email,:password)"),
                {"id": user.user_id, "email": user.email, "password": user.hashed_password},
            )
            await db.execute(
                text("INSERT INTO tenants(id,name) VALUES (:id,:name)"),
                {"id": tenant_id, "name": tenant_name},
            )
            await db.execute(
                text("""INSERT INTO tenant_memberships(id,tenant_id,user_id,role)
                VALUES (:id,:tenant,:user,'TENANT_ADMIN')"""),
                {"id": membership_id, "tenant": tenant_id, "user": user.user_id},
            )
            ctx = UserContext(tenant_id, user.user_id, membership_id, "TENANT_ADMIN")
            for name, key, public in [
                ("公共空间", "public", True),
                ("我的空间", "personal-" + membership_id, False),
            ]:
                space_id = str(uuid4())
                params = {
                    "id": space_id,
                    "tenant": tenant_id,
                    "name": name,
                    "key": key,
                    "member": membership_id,
                    "user": user.user_id,
                }
                await db.execute(
                    text(
                        """INSERT INTO spaces(id,tenant_id,name,space_key,created_by,created_by_membership_id)
                    VALUES (:id,:tenant,:name,:key,:user,:member)"""
                    ),
                    params,
                )
                await db.execute(
                    text("""INSERT INTO space_grants(tenant_id,space_id,subject_type,
                    tenant_membership_id,role,source,created_by_membership_id)
                    VALUES (:tenant,:id,'MEMBER',:member,'MANAGER','template',:member)"""),
                    params,
                )
                if public:
                    await db.execute(
                        text("""INSERT INTO space_grants(tenant_id,space_id,subject_type,
                        role,source,created_by_membership_id)
                        VALUES (:tenant,:id,'TENANT','VIEWER','template',:member)"""),
                        params,
                    )
                await write_audit(
                    db,
                    ctx,
                    "space.create",
                    "space",
                    space_id,
                    {"preset": "public" if public else "private"},
                )
            await write_audit(db, ctx, "tenant.create", "tenant", tenant_id)
        return user
