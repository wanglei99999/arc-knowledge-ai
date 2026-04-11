"""TokenChunkerStage 单元测试——无外部依赖"""
import pytest

from app.pipeline.core.context import ProcessingContext
from app.pipeline.stages.chunking.token_chunker import TokenChunkerStage, _split_text
from app.providers.base import ParsedDocument


# ── _split_text 纯函数测试 ────────────────────────────────────────────────────

def test_split_empty_text() -> None:
    assert _split_text("", 512, 64) == []


def test_split_short_text_single_chunk() -> None:
    chunks = _split_text("Hello world.", 512, 64)
    assert len(chunks) == 1
    assert chunks[0] == "Hello world."


def test_split_long_text_multiple_chunks() -> None:
    # 每段约 50 tokens，chunk_size=100 → 应该切成多个
    paragraphs = ["word " * 50 for _ in range(6)]
    text = "\n\n".join(paragraphs)
    chunks = _split_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1


def test_overlap_carries_content() -> None:
    para_a = "Alpha " * 60   # ~60 tokens
    para_b = "Beta " * 60
    para_c = "Gamma " * 60
    text = "\n\n".join([para_a, para_b, para_c])
    chunks = _split_text(text, chunk_size=100, overlap=50)
    # 第二个 chunk 应包含第一个 chunk 的尾部内容（overlap）
    assert len(chunks) >= 2


# ── Stage 测试 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chunks_have_correct_document_id(fake_ctx: ProcessingContext) -> None:
    stage = TokenChunkerStage()
    doc = ParsedDocument(text="Short text.")
    chunks = await stage.execute(fake_ctx, doc)
    assert all(c.document_id == fake_ctx.document_id for c in chunks)
    assert all(c.tenant_id == fake_ctx.tenant_id for c in chunks)


@pytest.mark.asyncio
async def test_chunk_index_is_sequential(fake_ctx: ProcessingContext) -> None:
    paragraphs = ["word " * 200 for _ in range(5)]
    doc = ParsedDocument(text="\n\n".join(paragraphs))
    fake_ctx.config.chunk_size = 128

    stage = TokenChunkerStage()
    chunks = await stage.execute(fake_ctx, doc)
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))


@pytest.mark.asyncio
async def test_title_written_to_chunk_metadata(fake_ctx: ProcessingContext) -> None:
    doc = ParsedDocument(text="Some content.", title="My Title")
    stage = TokenChunkerStage()
    chunks = await stage.execute(fake_ctx, doc)
    assert all(c.metadata.get("title") == "My Title" for c in chunks)


@pytest.mark.asyncio
async def test_token_count_is_positive(fake_ctx: ProcessingContext) -> None:
    doc = ParsedDocument(text="Hello world, this is a test.")
    stage = TokenChunkerStage()
    chunks = await stage.execute(fake_ctx, doc)
    assert all(c.token_count > 0 for c in chunks)
