from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError

from app.config.settings import settings
from app.domain.user import User
from app.infrastructure.postgres.repositories.user_repo import UserRepository
from app.infrastructure.redis.client import get_redis
from app.services.authorization_service import authorization

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthService:
    def __init__(self, user_repo=None):
        self._user_repo = user_repo or UserRepository()

    def _hash_password(self, password):
        return _pwd_context.hash(password)

    def _verify_password(self, plain, hashed):
        return _pwd_context.verify(plain, hashed)

    def _issue_access_token(self, ctx):
        return jwt.encode(
            {
                "sub": ctx.user_id,
                "tenant_id": ctx.tenant_id,
                "membership_id": ctx.membership_id,
                "type": "access",
                "exp": datetime.now(UTC) + timedelta(minutes=settings.jwt_access_expire_minutes),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

    async def _pair(self, ctx):
        refresh = str(uuid.uuid4())
        await get_redis().set(
            "refresh:" + refresh,
            json.dumps(
                {
                    "user_id": ctx.user_id,
                    "tenant_id": ctx.tenant_id,
                    "membership_id": ctx.membership_id,
                }
            ),
            ex=settings.jwt_refresh_expire_days * 86400,
        )
        return TokenPair(self._issue_access_token(ctx), refresh)

    async def register(self, email, password, tenant_name):
        email = email.strip().lower()
        if await self._user_repo.find_by_email(email):
            raise ValueError("Email already registered")
        user = User(email=email, hashed_password=self._hash_password(password))
        try:
            return await self._user_repo.register_organization(user, tenant_name)
        except IntegrityError as exc:
            raise ValueError("Email already registered") from exc

    async def login(self, email, password):
        user = await self._user_repo.find_by_email(email)
        if (
            not user
            or not user.is_active
            or not self._verify_password(password, user.hashed_password)
        ):
            raise ValueError("Invalid email or password")
        memberships = await self._user_repo.memberships(user.user_id)
        if not memberships:
            raise ValueError("No active tenant membership")
        if len(memberships) == 1:
            return await self.switch_tenant(user.user_id, memberships[0]["tenant_id"])
        token = jwt.encode(
            {
                "sub": user.user_id,
                "type": "tenant_selection",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        return {"selection_token": token, "memberships": memberships}

    async def switch_tenant(self, user_id, tenant_id):
        return await self._pair(await authorization.context(tenant_id, user_id))

    async def refresh(self, refresh_token):
        raw = await get_redis().get("refresh:" + refresh_token)
        if not raw:
            raise ValueError("Invalid or expired refresh token")
        try:
            data = json.loads(raw)
            ctx = await authorization.context(
                data["tenant_id"], data["user_id"], data["membership_id"]
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("Invalid refresh token") from exc
        return self._issue_access_token(ctx)

    async def logout(self, refresh_token, user_id):
        redis = get_redis()
        raw = await redis.get("refresh:" + refresh_token)
        if raw and json.loads(raw).get("user_id") == user_id:
            await redis.delete("refresh:" + refresh_token)
