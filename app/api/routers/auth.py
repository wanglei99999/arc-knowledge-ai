from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.api.dependencies import UserContext, require_login_identity, require_user
from app.services.auth_service import AuthService, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["identity"])
_service = AuthService()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    tenant_name: str = Field(min_length=1, max_length=256)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        return value


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(max_length=72)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        return RegisterBody.password_bytes(value)


class RefreshBody(BaseModel):
    refresh_token: str


class TenantBody(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)


@router.post("/register", status_code=201)
async def register(body: RegisterBody):
    if not body.tenant_name.strip():
        raise HTTPException(422, detail="组织名称不能为空")
    try:
        user = await _service.register(str(body.email), body.password, body.tenant_name.strip())
        return {"user_id": user.user_id, "email": user.email}
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.post("/login")
async def login(body: LoginBody):
    try:
        result = await _service.login(str(body.email), body.password)
        return asdict(result) if isinstance(result, TokenPair) else result
    except ValueError as exc:
        raise HTTPException(401, detail=str(exc)) from exc


@router.post("/switch-tenant")
async def switch_tenant(body: TenantBody, user_id: str = Depends(require_login_identity)):
    return asdict(await _service.switch_tenant(user_id, body.tenant_id))


@router.post("/refresh")
async def refresh(body: RefreshBody):
    try:
        return {"access_token": await _service.refresh(body.refresh_token), "token_type": "bearer"}
    except ValueError as exc:
        raise HTTPException(401, detail=str(exc)) from exc


@router.post("/logout", status_code=204)
async def logout(body: RefreshBody, user_id: str = Depends(require_login_identity)):
    await _service.logout(body.refresh_token, user_id)


@me_router.get("/context")
async def context(ctx: UserContext = Depends(require_user)):
    return asdict(ctx)


@me_router.get("/tenants")
async def tenants(user_id: str = Depends(require_login_identity)):
    return await _service._user_repo.memberships(user_id)
