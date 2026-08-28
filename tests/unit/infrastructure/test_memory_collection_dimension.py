from __future__ import annotations

from app.config.settings import settings
from app.infrastructure.milvus.memory_collection import _memory_dim


def test_memory_collection_uses_configured_embedding_dimension(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_embedding_dimensions", 768)

    assert _memory_dim() == 768
