from __future__ import annotations

from sqlalchemy import text
from app.domain.user import User
from app.infrastructure.postgres.client import get_session

class UserRepository:

    async def find_by_email(self,tenant_id:str,email:str)->User|None:
        sql = text("""
        SELECT id, tenant_id, email, hashed_password, is_active, created_at
        FROM users
        WHERE tenant_id = :tenant_id
            AND email = :email
    """)
        
        async with get_session() as session:
            result = await session.execute(sql,{
                "tenant_id": tenant_id,
                "email": email,
            })

            row = result.one_or_none()
            if row is None:
                return None
            #_mapping:将把数据库行 → 字典
            m = row._mapping
            return User(
                user_id=str(m["id"]),
                tenant_id=m["tenant_id"],
                email=m["email"],
                hashed_password=m["hashed_password"],
                is_active=m["is_active"],
                created_at=m["created_at"]
            )
    async def create(self,user:User)-> User:
        sql = text( """
            INSERT INTO users (id,tenant_id,email,hashed_password, is_active,created_at)
            VALUES (:id,:tenant_id,:email,:hashed_password,:is_active,:created_at)
        """
        )
        async with get_session() as session:
            await session.execute(sql,{
                "id": user.user_id,
                "tenant_id": user.tenant_id,
                "email": user.email,
                "hashed_password": user.hashed_password,
                "is_active": user.is_active,
                "created_at": user.created_at,
            })
            return user
