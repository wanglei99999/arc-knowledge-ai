from __future__ import annotations

from app.services.access_management_service import AccessManagementService
from app.services.authorization_service import authorization


class SpaceService:
    def __init__(self, management=None) -> None:
        self._management = management or AccessManagementService()

    async def list_spaces(self, tenant_id: str, user_id: str = "") -> list[dict]:
        return await authorization.list_spaces(tenant_id, user_id)

    async def create_space(
        self,
        tenant_id: str,
        name: str,
        user_id: str,
    ) -> dict:
        ctx = await authorization.context(tenant_id, user_id)
        return await self._management.create_space(ctx, name, "private", None, "VIEWER")

    async def delete_space(self, tenant_id: str, space_id: str, user_id: str = "") -> bool:
        ctx = await authorization.context(tenant_id, user_id)
        await self._management.update_space(ctx, space_id, status="archived")
        return True

    async def restore_space(self, tenant_id: str, space_id: str, user_id: str = "") -> bool:
        ctx = await authorization.context(tenant_id, user_id)
        await self._management.update_space(ctx, space_id, status="active")
        return True
