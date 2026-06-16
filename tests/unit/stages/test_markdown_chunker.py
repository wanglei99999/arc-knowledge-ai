"""MarkdownChunkerStage 单元测试——无外部依赖。

结构感知分片：识别 Markdown 的 heading / code / table 块，
代码块与表格整块保留不切断，标题作为后续 chunk 的 section 上下文。
"""

import pytest

from app.pipeline.core.context import ProcessingContext
from app.pipeline.stages.chunking.markdown_chunker import (
    Block,
    MarkdownChunkerStage,
    blocks_to_chunks,
    parse_blocks,
)
from app.providers.base import ParsedDocument

# ── parse_blocks 纯函数测试 ──────────────────────────────────────────────────


def test_parse_empty_text_returns_no_blocks() -> None:
    assert parse_blocks("") == []


def test_parse_single_paragraph() -> None:
    blocks = parse_blocks("这是一段普通正文。")
    assert len(blocks) == 1
    assert blocks[0].kind == "paragraph"
    assert blocks[0].content == "这是一段普通正文。"


def test_parse_heading_records_level() -> None:
    blocks = parse_blocks("## 安装说明")
    assert len(blocks) == 1
    assert blocks[0].kind == "heading"
    assert blocks[0].level == 2
    assert blocks[0].content == "## 安装说明"


def test_parse_code_fence_is_single_block() -> None:
    text = "```python\nx = 1\n\ny = 2\n```"
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].kind == "code"
    # 代码块内部的空行不能被当作段落分隔符拆开
    assert "x = 1" in blocks[0].content
    assert "y = 2" in blocks[0].content


def test_parse_table_is_single_block() -> None:
    text = "| 组件 | 版本 |\n|------|------|\n| Milvus | 2.4 |\n| Redis | 7.0 |"
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].kind == "table"
    assert "Milvus" in blocks[0].content
    assert "Redis" in blocks[0].content


def test_parse_mixed_document_preserves_order() -> None:
    text = "# 标题\n\n" "正文段落。\n\n" "```python\ncode_here()\n```\n\n" "结尾段落。"
    blocks = parse_blocks(text)
    kinds = [b.kind for b in blocks]
    assert kinds == ["heading", "paragraph", "code", "paragraph"]


# ── blocks_to_chunks 策略测试 ────────────────────────────────────────────────


def test_heading_becomes_section_not_chunk() -> None:
    blocks = [
        Block(kind="heading", content="## 第一章", level=2),
        Block(kind="paragraph", content="第一章的内容。"),
    ]
    pieces = blocks_to_chunks(blocks, chunk_size=512, overlap=64)
    # 标题不单独成 chunk
    assert len(pieces) == 1
    assert pieces[0].chunk_type == "text"
    assert pieces[0].section == "## 第一章"
    assert "第一章的内容" in pieces[0].content


def test_code_block_kept_whole_even_if_oversized() -> None:
    big_code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(200)) + "\n```"
    blocks = [Block(kind="code", content=big_code)]
    pieces = blocks_to_chunks(blocks, chunk_size=50, overlap=10)
    # 即使超过 chunk_size，代码块也整块保留为一个 chunk
    assert len(pieces) == 1
    assert pieces[0].chunk_type == "code"
    assert pieces[0].content == big_code


def test_table_block_kept_whole() -> None:
    table = "| A | B |\n|---|---|\n| 1 | 2 |"
    blocks = [Block(kind="table", content=table)]
    pieces = blocks_to_chunks(blocks, chunk_size=512, overlap=64)
    assert len(pieces) == 1
    assert pieces[0].chunk_type == "table"
    assert pieces[0].content == table


def test_long_paragraphs_split_into_multiple_text_chunks() -> None:
    paragraphs = [Block(kind="paragraph", content="word " * 60) for _ in range(6)]
    pieces = blocks_to_chunks(paragraphs, chunk_size=100, overlap=20)
    assert len(pieces) > 1
    assert all(p.chunk_type == "text" for p in pieces)


def test_section_updates_across_headings() -> None:
    blocks = [
        Block(kind="heading", content="# 一", level=1),
        Block(kind="paragraph", content="一的内容。"),
        Block(kind="heading", content="# 二", level=1),
        Block(kind="paragraph", content="二的内容。"),
    ]
    pieces = blocks_to_chunks(blocks, chunk_size=512, overlap=64)
    sections = {p.content[:3]: p.section for p in pieces}
    assert sections["一的内"] == "# 一"
    assert sections["二的内"] == "# 二"


# ── Stage 集成测试（用 fake_ctx） ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage_writes_section_and_type_to_metadata(fake_ctx: ProcessingContext) -> None:
    text = "## 章节\n\n这是章节正文内容。"
    doc = ParsedDocument(text=text, title="文档标题")
    stage = MarkdownChunkerStage()
    chunks = await stage.execute(fake_ctx, doc)
    assert len(chunks) >= 1
    c = chunks[0]
    assert c.metadata.get("title") == "文档标题"
    assert c.metadata.get("section") == "## 章节"
    assert c.metadata.get("chunk_type") == "text"


@pytest.mark.asyncio
async def test_stage_does_not_split_code_block(fake_ctx: ProcessingContext) -> None:
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(100)) + "\n```"
    doc = ParsedDocument(text=f"# 示例\n\n{code}")
    fake_ctx.config.chunk_size = 50
    stage = MarkdownChunkerStage()
    chunks = await stage.execute(fake_ctx, doc)
    code_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "code"]
    assert len(code_chunks) == 1
    assert "x0 = 0" in code_chunks[0].content
    assert "x99 = 99" in code_chunks[0].content


@pytest.mark.asyncio
async def test_stage_chunk_index_is_sequential(fake_ctx: ProcessingContext) -> None:
    paragraphs = "\n\n".join("word " * 200 for _ in range(5))
    doc = ParsedDocument(text=paragraphs)
    fake_ctx.config.chunk_size = 128
    stage = MarkdownChunkerStage()
    chunks = await stage.execute(fake_ctx, doc)
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))
