from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.document import DocumentChunk
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage
from app.pipeline.stages.chunking.token_chunker import _split_text
from app.providers.base import ParsedDocument
from app.utils.tokenizer import count_tokens

# MinerU 输出的是结构化 Markdown，标题 / 代码块 / 表格的边界已由格式标记给出。
# 结构感知分片识别这些块，对代码块和表格整块保留（不切断），
# 标题作为后续 chunk 的 section 上下文，普通正文复用 token_chunker 的滑窗切分。

_HEADING_RE = re.compile(r"^#{1,6}\s")
_FENCE_RE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class Block:
    """Markdown 解析后的结构块。"""

    kind: str  # "heading" | "code" | "table" | "paragraph"
    content: str
    level: int = 0  # 仅 heading 有意义（# 的数量）


@dataclass(frozen=True)
class ChunkPiece:
    """切割产物：携带所属章节与块类型的文本片段。"""

    content: str
    section: str  # 最近的 heading 原文，无则 ""
    chunk_type: str  # "text" | "code" | "table"


def _is_table_separator(line: str) -> bool:
    """识别 Markdown 表格分隔行，如 |---|---| 或 :--|--:。"""
    s = line.strip()
    if "-" not in s or "|" not in s:
        return False
    return all(c in "|-: " for c in s)


def _is_table_start(lines: list[str], i: int) -> bool:
    """当前行含 | 且下一行是分隔行，判定为表格起始。"""
    return "|" in lines[i] and i + 1 < len(lines) and _is_table_separator(lines[i + 1])


def parse_blocks(text: str) -> list[Block]:
    """逐行状态机，将 Markdown 文本解析为有序 Block 列表。"""
    lines = text.split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 1. 代码围栏：收集到匹配的关闭围栏，内部空行不拆分
        fence_match = _FENCE_RE.match(stripped)
        if fence_match:
            fence = fence_match.group(1)
            code_lines = [line]
            i += 1
            while i < n:
                code_lines.append(lines[i])
                if lines[i].strip().startswith(fence):
                    i += 1
                    break
                i += 1
            blocks.append(Block(kind="code", content="\n".join(code_lines)))
            continue

        # 2. 标题（单行）
        if _HEADING_RE.match(stripped):
            level = len(stripped) - len(stripped.lstrip("#"))
            blocks.append(Block(kind="heading", content=stripped, level=level))
            i += 1
            continue

        # 3. 表格：整块连续含 | 的行
        if _is_table_start(lines, i):
            table_lines = [line]
            i += 1
            while i < n and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            blocks.append(Block(kind="table", content="\n".join(table_lines)))
            continue

        # 4. 空行跳过
        if stripped == "":
            i += 1
            continue

        # 5. 普通段落：累积到空行或下一个特殊块
        para_lines = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            nxt_stripped = nxt.strip()
            if nxt_stripped == "":
                break
            if _FENCE_RE.match(nxt_stripped) or _HEADING_RE.match(nxt_stripped):
                break
            if _is_table_start(lines, i):
                break
            para_lines.append(nxt)
            i += 1
        blocks.append(Block(kind="paragraph", content="\n".join(para_lines).strip()))

    return blocks


def blocks_to_chunks(
    blocks: list[Block],
    chunk_size: int,
    overlap: int,
) -> list[ChunkPiece]:
    """按块类型分策略合并：标题→section，代码/表格整块，正文滑窗切分。"""
    pieces: list[ChunkPiece] = []
    section = ""
    para_buf: list[str] = []

    def flush_paragraphs() -> None:
        if not para_buf:
            return
        joined = "\n\n".join(para_buf)
        for sub in _split_text(joined, chunk_size, overlap):
            pieces.append(ChunkPiece(content=sub, section=section, chunk_type="text"))
        para_buf.clear()

    for block in blocks:
        if block.kind == "heading":
            flush_paragraphs()
            section = block.content
        elif block.kind in ("code", "table"):
            flush_paragraphs()
            # 代码块与表格整块保留，即使超过 chunk_size 也不切断
            pieces.append(ChunkPiece(content=block.content, section=section, chunk_type=block.kind))
        else:  # paragraph
            para_buf.append(block.content)

    flush_paragraphs()
    return pieces


@registry.stage("markdown_chunker")
class MarkdownChunkerStage(BaseStage[ParsedDocument, list[DocumentChunk]]):
    """
    结构感知切分 Stage。

    识别 Markdown 的标题 / 代码块 / 表格结构：
    - 代码块、表格整块保留，避免检索到半段代码或缺表头的表格
    - 标题写入 chunk metadata 的 section 字段，保留章节上下文
    - 普通正文复用 token_chunker 的 token 滑窗算法

    chunk_size / chunk_overlap 从 ctx.config 读取，与 token_chunker 一致。
    """

    name = "markdown_chunker"

    async def _execute(
        self,
        ctx: ProcessingContext,
        input: ParsedDocument,
    ) -> list[DocumentChunk]:
        blocks = parse_blocks(input.text)
        pieces = blocks_to_chunks(
            blocks,
            chunk_size=ctx.config.chunk_size,
            overlap=ctx.config.chunk_overlap,
        )

        chunks: list[DocumentChunk] = []
        for idx, piece in enumerate(pieces):
            metadata: dict = {"chunk_type": piece.chunk_type}
            if input.title:
                metadata["title"] = input.title
            if piece.section:
                metadata["section"] = piece.section
            chunks.append(
                DocumentChunk(
                    document_id=ctx.document_id,
                    tenant_id=ctx.tenant_id,
                    content=piece.content,
                    chunk_index=idx,
                    token_count=count_tokens(piece.content),
                    metadata=metadata,
                )
            )
        return chunks
