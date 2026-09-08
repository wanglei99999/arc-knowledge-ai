from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config.settings import settings
from app.domain.authorization import UserContext
from app.services.authorization_service import authorization

_bearer = HTTPBearer(auto_error=False)


def decode_identity(credentials, accepted_types):
    try:
        if credentials is None:
            raise ValueError("Missing token")
        data = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "type"]},
        )
        if not isinstance(data["sub"], str):
            raise ValueError("Invalid user")
        UUID(data["sub"])
        if data["type"] not in accepted_types:
            raise ValueError("Wrong token purpose")
        return data
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"}
        ) from exc


async def require_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> UserContext:
    data = decode_identity(credentials, {"access"})
    try:
        if not isinstance(data.get("membership_id"), str):
            raise ValueError("Invalid membership")
        UUID(data["membership_id"])
        tenant_id = data["tenant_id"]
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Invalid tenant")
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(401, detail="Invalid tenant context") from exc
    return await authorization.context(tenant_id, data["sub"], data["membership_id"])


async def require_tenant(ctx: UserContext = Depends(require_user)) -> str:
    return ctx.tenant_id


async def require_tenant_admin(ctx: UserContext = Depends(require_user)) -> UserContext:
    return await authorization.require_admin(ctx.tenant_id, ctx.user_id)


async def require_login_identity(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> str:
    data = decode_identity(credentials, {"access", "tenant_selection"})
    if data["type"] == "access":
        return (await require_user(credentials)).user_id
    from app.infrastructure.postgres.repositories.user_repo import UserRepository

    user = await UserRepository().find_by_id(data["sub"])
    if not user or not user.is_active:
        raise HTTPException(401, detail="Inactive user")
    return user.user_id
