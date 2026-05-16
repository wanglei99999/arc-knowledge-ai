from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr

from app.api.dependencies import UserContext, require_user
from app.services.auth_service import AuthService, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])
_service = AuthService()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class RefreshBody(BaseModel):
    refresh_token: str


class LogoutBody(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterBody,
    x_tenant_id: str = Header(alias="X-Tenant-Id"),
) -> dict:
    try:
        user = await _service.register(x_tenant_id, body.email, body.password)
        return {"user_id": user.user_id, "email": user.email}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/login")
async def login(
    body: LoginBody,
    x_tenant_id: str = Header(alias="X-Tenant-Id"),
) -> TokenOut:
    try:
        pair = await _service.login(x_tenant_id, body.email, body.password)
        return TokenOut(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            token_type=pair.token_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@router.post("/refresh")
async def refresh(body: RefreshBody) -> dict:
    try:
        access_token = await _service.refresh(body.refresh_token)
        return {"access_token": access_token, "token_type": "bearer"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: LogoutBody,
    _: UserContext = Depends(require_user),
) -> None:
    await _service.logout(body.refresh_token)
