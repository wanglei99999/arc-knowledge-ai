from __future__ import annotations

import pytest

from app.services.space_service import SpaceService


class _SpaceRepository:
    def __init__(self) -> None:
        self.result = True
        self.calls: list[tuple[str, str]] = []

    async def restore(self, tenant_id: str, space_id: str) -> bool:
        self.calls.append((tenant_id, space_id))
        return self.result


@pytest.mark.asyncio
async def test_restore_space_delegates_tenant_and_space_in_order() -> None:
    repository = _SpaceRepository()
    service = SpaceService(repo=repository)

    restored = await service.restore_space("tenant-1", "space-1")

    assert restored is True
    assert repository.calls == [("tenant-1", "space-1")]
