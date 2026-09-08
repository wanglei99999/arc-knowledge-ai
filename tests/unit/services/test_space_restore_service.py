from __future__ import annotations

import pytest

from app.domain.authorization import AuthorizationError
from app.services.space_service import SpaceService


@pytest.mark.asyncio
async def test_legacy_space_facade_requires_explicit_identity() -> None:
    service = SpaceService()
    with pytest.raises(AuthorizationError) as error:
        await service.restore_space("tenant-1", "space-1")
    assert error.value.status_code == 401
