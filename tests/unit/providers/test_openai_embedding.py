from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.config.settings import Settings
from app.pipeline.core.context import ProcessingContext
from app.providers.embedding.openai_embedding import OpenAIEmbeddingProvider


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.last_request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.last_request = kwargs
        return SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.25, 0.75])]
        )


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddings()


def test_embedding_dimensions_can_be_configured() -> None:
    config = Settings(_env_file=None, openai_embedding_dimensions=768)

    assert config.openai_embedding_dimensions == 768


@pytest.mark.asyncio
async def test_embed_requests_configured_dimensions() -> None:
    provider = OpenAIEmbeddingProvider(api_key="test", model="qwen3-embedding:4b")
    fake_client = _FakeOpenAIClient()
    provider._client = fake_client  # type: ignore[assignment]

    embeddings = await provider.embed(
        cast(ProcessingContext, None),
        ["深圳大学知识库"],
    )

    assert embeddings == [[0.25, 0.75]]
    assert fake_client.embeddings.last_request == {
        "model": "qwen3-embedding:4b",
        "input": ["深圳大学知识库"],
        "dimensions": 1536,
    }
    assert provider.get_dimension() == 1536
