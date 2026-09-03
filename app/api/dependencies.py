from __future__ import annotations

import logging
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config.settings import settings

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


@dataclass
class UserContext:
    """JWT 解析后的用户身份（tenant_id + user_id）"""

    tenant_id: str
    user_id: str


async def require_tenant(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> str:
    """
    FastAPI Depends：验证身份并提取 tenant_id。

    优先级：
    1. Authorization: Bearer <JWT>  —— 生产环境
       JWT payload 必须包含 tenant_id claim。
    2. X-Tenant-Id header（fallback） —— 开发 / 内部服务调用
       仅在 app_env != "production" 时允许。

    JWT payload 格式：
        { "sub": "<user-id>", "tenant_id": "<tenant>", "exp": <unix_ts> }
    """
    # ── 路径 1：JWT ───────────────────────────────────────────────────────────
    if credentials is not None:
        try:
            payload = jwt.decode(
                credentials.credentials,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing tenant_id claim",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return str(tenant_id)

    # ── 路径 2：X-Tenant-Id（非生产环境 fallback）───────────────────────────
    if settings.app_env != "production" and x_tenant_id:
        if x_tenant_id.strip():
            logger.debug("Using X-Tenant-Id header (non-production): %s", x_tenant_id)
            return x_tenant_id.strip()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: provide Authorization: Bearer <token>",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> UserContext:
    """
    FastAPI Depends：验证身份并返回 UserContext（tenant_id + user_id）。

    JWT payload 格式：{ "sub": "<user_id>", "tenant_id": "<tenant>", "exp": ... }
    非生产环境 fallback：X-Tenant-Id header → user_id 固定为 "dev-user"
    """
    if credentials is not None:
        try:
            payload = jwt.decode(
                credentials.credentials,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        tenant_id = payload.get("tenant_id")
        user_id = payload.get("sub")
        if not tenant_id or not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing tenant_id or sub claim",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return UserContext(tenant_id=str(tenant_id), user_id=str(user_id))

    if settings.app_env != "production" and x_tenant_id and x_tenant_id.strip():
        logger.debug("require_user fallback via X-Tenant-Id: %s", x_tenant_id)
        return UserContext(tenant_id=x_tenant_id.strip(), user_id="dev-user")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: provide Authorization: Bearer <token>",
        headers={"WWW-Authenticate": "Bearer"},
    )
