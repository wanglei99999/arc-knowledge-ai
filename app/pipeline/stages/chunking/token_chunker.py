from __future__ import annotations

import re

from app.domain.document import DocumentChunk
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.stage import BaseStage
from app.providers.base import ParsedDocument


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（1 token ≈ 4 字符），无外部依赖"""
    return max(1, len(text) // 4)


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    按段落滑窗切分文本。

    算法：
    1. 按连续空行分割成段落
    2. 累积段落直到超过 chunk_size
    3. 超过后保存当前 chunk，保留最后 overlap 个 token 的段落作为重叠
    """
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paragraphs:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)

        # 单段落超过 chunk_size 时强制按句子再切
        if para_tokens > chunk_size:
            if current:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            sentences = re.split(r"(?<=[。！？.!?])\s*", para)
            for sent in sentences:
                s_tokens = _estimate_tokens(sent)
                if current_tokens + s_tokens > chunk_size and current:
                    chunks.append("\n\n".join(current))
                    current, current_tokens = [], 0
                current.append(sent)
                current_tokens += s_tokens
            continue

        if current_tokens + para_tokens > chunk_size and current:
            chunks.append("\n\n".join(current))
            # overlap：保留末尾若干段落（累积不超过 overlap token）
            tail: list[str] = []
            tail_tokens = 0
            for p in reversed(current):
                t = _estimate_tokens(p)
                if tail_tokens + t > overlap:
                    break
                tail.insert(0, p)
                tail_tokens += t
            current, current_tokens = tail, tail_tokens

        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks if chunks else [text]


class TokenChunkerStage(BaseStage[ParsedDocument, list[DocumentChunk]]):
    """
    基于 token 估算的滑窗切分 Stage。

    chunk_size 和 chunk_overlap 从 ctx.config 读取，
    支持按租户差异化配置切片粒度。
    """

    name = "token_chunker"

    async def _execute(
        self,
        ctx: ProcessingContext,
        input: ParsedDocument,
    ) -> list[DocumentChunk]:
        chunk_size = ctx.config.chunk_size
        overlap = ctx.config.chunk_overlap

        raw_chunks = _split_text(input.text, chunk_size, overlap)

        return [
            DocumentChunk(
                document_id=ctx.document_id,
                tenant_id=ctx.tenant_id,
                content=text,
                chunk_index=idx,
                token_count=_estimate_tokens(text),
                metadata={"title": input.title} if input.title else {},
            )
            for idx, text in enumerate(raw_chunks)
        ]
