from __future__ import annotations

from app.domain.space import Space
from app.infrastructure.postgres.repositories.space_repo import SpaceRepository

class SpaceService:

    def __init__(self) -> None:
        self._repo = SpaceRepository()

    async def list_spaces(self, tenant_id: str) -> list[Space]:
        return await self._repo.list_by_tenant(tenant_id)

    async def create_space(
        self,
        tenant_id: str,
        name: str,
        user_id: str,
    ) -> Space:
        return await self._repo.create(tenant_id, name, created_by=user_id)