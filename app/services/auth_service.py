from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.config.settings import settings
from app.domain.user import User
from app.infrastructure.postgres.repositories.space_repo import SpaceRepository
from app.infrastructure.postgres.repositories.user_repo import UserRepository
from app.infrastructure.redis.client import get_redis

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthService:
    def __init__(self) -> None:
        self._user_repo = UserRepository()
        self._space_repo = SpaceRepository()

    # 把明文密码交给 bcrypt 加密，返回哈希字符串，存数据库
    def _hash_password(self, password: str) -> str:
        return _pwd_context.hash(password)

    # 登录时用，拿明文和哈希比对，返回 True/False（bcrypt 内部会把 salt 从哈希里提取出来再比对）
    def _verify_password(self, plain: str, hashed: str) -> bool:
        return _pwd_context.verify(plain, hashed)

    # 签发 JWT，payload 里的 sub 和 tenant_id 格式和 dependencies.py 里解析的完全对应
    def _issue_access_token(self, user: User) -> str:
        expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_expire_minutes)
        payload = {
            "sub": user.user_id,  # 主题
            "tenant_id": user.tenant_id,
            "exp": expire,  # 过期时间
        }
        return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    # 生成随机 UUID 存进 Redis。key 为 refresh:{uuid}，value 为 user_id:tenant_id，
    # TTL 使用配置的天数换算成秒。
    async def _issue_refresh_token(self, user: User) -> str:
        token = str(uuid.uuid4())
        redis = get_redis()
        ttl = settings.jwt_refresh_expire_days * 24 * 60 * 60
        await redis.set(f"refresh:{token}", f"{user.user_id}:{user.tenant_id}", ex=ttl)
        return token

    # 先查邮箱是否已存在，存在就报错；不存在就哈希密码、创建 User 写库
    async def register(self, tenant_id: str, email: str, password: str) -> User:
        existing = await self._user_repo.find_by_email(tenant_id, email)
        if existing:
            raise ValueError("Email already registered")
        user = User(tenant_id=tenant_id, email=email, hashed_password=self._hash_password(password))
        created = await self._user_repo.create(user)
        await self._space_repo.ensure_default(tenant_id)
        return created

    # 查用户，验密码；两个条件用 or 合并成一个错误信息，防止攻击者通过错误信息判断"邮箱存不存在"
    async def login(self, tenant_id: str, email: str, password: str) -> TokenPair:
        user = await self._user_repo.find_by_email(tenant_id, email)
        if (
            not user
            or not user.is_active
            or not self._verify_password(password, user.hashed_password)
        ):
            raise ValueError("Invalid email or password")
        access_token = self._issue_access_token(user)
        refresh_token = await self._issue_refresh_token(user)
        return TokenPair(access_token=access_token, refresh_token=refresh_token)

    # 从 Redis 取出 user_id:tenant_id，最多拆一次，避免 tenant_id 中的冒号被误拆，
    # 然后重新签发 access token。
    async def refresh(self, refresh_token: str) -> str:
        redis = get_redis()
        value = await redis.get(f"refresh:{refresh_token}")
        if not value:
            raise ValueError("Invalid or expired refresh token")
        user_id, tenant_id = value.split(":", 1)
        user = User(user_id=user_id, tenant_id=tenant_id, email="", hashed_password="")
        return self._issue_access_token(user)

    # 先验证 token 归属当前用户，再删除——防止持有有效 access token 的攻击者删掉他人的 refresh token
    async def logout(self, refresh_token: str, user_id: str) -> None:
        redis = get_redis()
        value = await redis.get(f"refresh:{refresh_token}")
        if value:
            stored_user_id, _ = value.split(":", 1)
            if stored_user_id == user_id:
                await redis.delete(f"refresh:{refresh_token}")
