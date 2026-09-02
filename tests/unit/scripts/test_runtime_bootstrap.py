from unittest.mock import AsyncMock, Mock

import pytest

from scripts.runtime import bootstrap as target


@pytest.mark.asyncio
async def test_bootstrap_is_ordered_and_repeatable(monkeypatch):
    events: list[str] = []
    migrate = AsyncMock(side_effect=lambda: events.append("database"))
    ensure_bucket = Mock(side_effect=lambda *_: events.append("minio"))
    ensure_index = Mock(side_effect=lambda *_: events.append("elasticsearch"))
    monkeypatch.setattr(target, "migrate", migrate)
    monkeypatch.setattr(target, "get_minio_client", lambda: object())
    monkeypatch.setattr(target, "_ensure_bucket", ensure_bucket)
    monkeypatch.setattr(target, "get_es_client", lambda: object())
    monkeypatch.setattr(target, "_ensure_index", ensure_index)

    await target.bootstrap()
    await target.bootstrap()

    assert events == [
        "database",
        "minio",
        "elasticsearch",
        "database",
        "minio",
        "elasticsearch",
    ]
