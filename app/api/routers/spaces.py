from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import UserContext, require_user
from app.services.space_service import SpaceService

router = APIRouter(prefix="/spaces", tags=["spaces"])

_service = SpaceService()


class CreateSpaceBody(BaseModel):
    name: str


class SpaceOut(BaseModel):
    space_id: str
    space_key: str
    name: str
    status: str
    created_by: str | None = None


@router.get("", response_model=list[SpaceOut])
async def list_spaces(ctx: UserContext = Depends(require_user))-> list[SpaceOut]:
    spaces = await _service.list_spaces(ctx.tenant_id)
    return [
        SpaceOut(
            space_id=s.space_id,
            space_key=s.space_key,
            name=s.name,
            status=s.status,
            created_by=s.created_by,
        )
        for s in spaces
    ]


@router.post("", response_model=SpaceOut, status_code=201)
async def create_space(
    body: CreateSpaceBody,
    ctx: UserContext = Depends(require_user),
) -> SpaceOut:
    space = await _service.create_space(ctx.tenant_id, body.name, ctx.user_id)
    return SpaceOut(
        space_id=space.space_id,
        space_key=space.space_key,
        name=space.name,
        status=space.status,
        created_by=space.created_by,
    )
    

