"""EmbedStage 单元测试——用 FakeEmbeddingProvider 替代真实 API"""
import pytest

from app.domain.document import DocumentChunk
from app.pipeline.core.context import ProcessingContext
from app.pipeline.stages.embedding.embed_stage import EmbedStage
from app.providers.base import EmbeddingProvider, HealthStatus


class FakeEmbeddingProvider(EmbeddingProvider):
    """测试用 fake provider，返回固定长度的零向量"""

    provider_id = "fake_embedding"
    DIM = 8

    async def embed(self, ctx: ProcessingContext, texts: list[str]) -> list[list[float]]:
        return [[float(i)] * self.DIM for i in range(len(texts))]

    def get_dimension(self) -> int:
        return self.DIM

    def get_model_name(self) -> str:
        return "fake-model"

    async def health_check(self) -> HealthStatus:
        return HealthStatus.HEALTHY


def _make_chunks(n: int, ctx: ProcessingContext) -> list[DocumentChunk]:
    return [
        DocumentChunk(
            document_id=ctx.document_id,
            tenant_id=ctx.tenant_id,
            content=f"chunk content {i}",
            chunk_index=i,
            token_count=10,
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_embed_attaches_vectors(fake_ctx: ProcessingContext) -> None:
    stage = EmbedStage(provider=FakeEmbeddingProvider())
    chunks = _make_chunks(3, fake_ctx)
    result = await stage.execute(fake_ctx, chunks)
    assert all(c.embedding is not None for c in result)
    assert all(len(c.embedding) == FakeEmbeddingProvider.DIM for c in result)


@pytest.mark.asyncio
async def test_embed_preserves_order(fake_ctx: ProcessingContext) -> None:
    stage = EmbedStage(provider=FakeEmbeddingProvider(), batch_size=2)
    chunks = _make_chunks(5, fake_ctx)
    result = await stage.execute(fake_ctx, chunks)
    assert [c.chunk_index for c in result] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_embed_empty_input(fake_ctx: ProcessingContext) -> None:
    stage = EmbedStage(provider=FakeEmbeddingProvider())
    result = await stage.execute(fake_ctx, [])
    assert result == []


@pytest.mark.asyncio
async def test_embed_writes_model_to_context(fake_ctx: ProcessingContext) -> None:
    stage = EmbedStage(provider=FakeEmbeddingProvider())
    chunks = _make_chunks(2, fake_ctx)
    await stage.execute(fake_ctx, chunks)
    assert fake_ctx.metadata["embedding_model"] == "fake-model"
    assert fake_ctx.metadata["embedding_dimension"] == FakeEmbeddingProvider.DIM


@pytest.mark.asyncio
async def test_embed_batching(fake_ctx: ProcessingContext) -> None:
    """batch_size=2 时 10 个 chunk 应分 5 批处理，结果数量不变"""
    stage = EmbedStage(provider=FakeEmbeddingProvider(), batch_size=2)
    chunks = _make_chunks(10, fake_ctx)
    result = await stage.execute(fake_ctx, chunks)
    assert len(result) == 10
    assert all(c.embedding is not None for c in result)
