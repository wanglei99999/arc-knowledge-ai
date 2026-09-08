from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import UserContext, require_user
from app.api.routers.access import CreateSpaceBody, NameBody, service
from app.services.authorization_service import authorization

router = APIRouter(prefix="/spaces", tags=["spaces"])


@router.get("")
async def list_spaces(ctx: UserContext = Depends(require_user)):
    return await authorization.list_spaces(ctx.tenant_id, ctx.user_id)


@router.post("", status_code=201)
async def create_space(body: CreateSpaceBody, ctx: UserContext = Depends(require_user)):
    return await service.create_space(ctx, **body.model_dump(mode="json"))


@router.patch("/{space_id}", status_code=204)
async def rename_space(space_id: UUID, body: NameBody, ctx: UserContext = Depends(require_user)):
    await service.update_space(ctx, str(space_id), name=body.name)


@router.delete("/{space_id}", status_code=204)
async def delete_space(space_id: UUID, ctx: UserContext = Depends(require_user)):
    await service.update_space(ctx, str(space_id), status="archived")


@router.post("/{space_id}/restore", status_code=204)
async def restore_space(space_id: UUID, ctx: UserContext = Depends(require_user)):
    await service.update_space(ctx, str(space_id), status="active")
