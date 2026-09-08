from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.api.dependencies import UserContext, require_user
from app.services.access_management_service import AccessManagementService

router = APIRouter(tags=["permissions"])
service = AccessManagementService()


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class CreateSpaceBody(NameBody):
    preset: Literal["private", "group", "public"] = "private"
    group_id: UUID | None = None
    group_role: Literal["VIEWER", "EDITOR"] = "EDITOR"


class GrantBody(BaseModel):
    subject_type: Literal["TENANT", "GROUP", "MEMBER"]
    role: Literal["VIEWER", "EDITOR", "MANAGER"]
    group_id: UUID | None = None
    tenant_membership_id: UUID | None = None


class AddMemberBody(BaseModel):
    email: EmailStr
    role: Literal["MEMBER", "TENANT_ADMIN"] = "MEMBER"


class UpdateMemberBody(BaseModel):
    role: Literal["MEMBER", "TENANT_ADMIN"] | None = None
    status: Literal["active", "suspended", "left"] | None = None


class GroupBody(NameBody):
    parent_group_id: UUID | None = None


class UpdateGroupBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    parent_group_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        return NameBody.nonblank(value) if value is not None else None


class GroupMemberBody(BaseModel):
    membership_id: UUID


class TakeoverBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.get("/tenant/directory")
async def directory(ctx: UserContext = Depends(require_user)):
    return await service.directory(ctx)


@router.get("/tenant/members")
async def members(ctx: UserContext = Depends(require_user)):
    return (await service.directory(ctx, admin=True))["members"]


@router.post("/tenant/members", status_code=201)
async def add_member(body: AddMemberBody, ctx: UserContext = Depends(require_user)):
    return await service.add_member(ctx, str(body.email), body.role)


@router.patch("/tenant/members/{membership_id}")
async def update_member(
    membership_id: UUID, body: UpdateMemberBody, ctx: UserContext = Depends(require_user)
):
    return await service.update_member(ctx, str(membership_id), **body.model_dump())


@router.get("/groups")
async def groups(ctx: UserContext = Depends(require_user)):
    return (await service.directory(ctx, admin=True))["groups"]


@router.post("/groups", status_code=201)
async def create_group(body: GroupBody, ctx: UserContext = Depends(require_user)):
    return await service.save_group(ctx, **body.model_dump(mode="json"))


@router.patch("/groups/{group_id}")
async def update_group(
    group_id: UUID, body: UpdateGroupBody, ctx: UserContext = Depends(require_user)
):
    return await service.save_group(
        ctx,
        group_id=str(group_id),
        change_parent="parent_group_id" in body.model_fields_set,
        **body.model_dump(mode="json"),
    )


@router.delete("/groups/{group_id}", status_code=204)
async def archive_group(group_id: UUID, ctx: UserContext = Depends(require_user)):
    await service.save_group(ctx, group_id=str(group_id), change_parent=False, archive=True)


@router.get("/groups/{group_id}/members")
async def group_members(group_id: UUID, ctx: UserContext = Depends(require_user)):
    return await service.group_members(ctx, str(group_id))


@router.post("/groups/{group_id}/members", status_code=201)
async def join_group(
    group_id: UUID, body: GroupMemberBody, ctx: UserContext = Depends(require_user)
):
    return await service.group_members(ctx, str(group_id), membership_id=str(body.membership_id))


@router.delete("/groups/{group_id}/members/{membership_id}", status_code=204)
async def leave_group(
    group_id: UUID, membership_id: UUID, ctx: UserContext = Depends(require_user)
):
    await service.group_members(ctx, str(group_id), membership_id=str(membership_id), remove=True)


@router.get("/spaces/{space_id}/grants")
async def grants(space_id: UUID, ctx: UserContext = Depends(require_user)):
    return await service.grants(ctx, str(space_id))


@router.put("/spaces/{space_id}/grants")
async def put_grant(space_id: UUID, body: GrantBody, ctx: UserContext = Depends(require_user)):
    return await service.put_grant(ctx, str(space_id), **body.model_dump(mode="json"))


@router.delete("/spaces/{space_id}/grants/{grant_id}", status_code=204)
async def revoke_grant(space_id: UUID, grant_id: UUID, ctx: UserContext = Depends(require_user)):
    await service.revoke_grant(ctx, str(space_id), str(grant_id))


@router.get("/tenant/spaces")
async def tenant_spaces(ctx: UserContext = Depends(require_user)):
    return await service.tenant_spaces(ctx)


@router.post("/tenant/spaces/{space_id}/takeover")
async def takeover(space_id: UUID, body: TakeoverBody, ctx: UserContext = Depends(require_user)):
    await service.takeover(ctx, str(space_id), body.reason)
    return {"message": "空间已接管，授权和原因已记录"}


@router.get("/tenant/audit")
async def audit(ctx: UserContext = Depends(require_user)):
    return await service.audit_events(ctx)
