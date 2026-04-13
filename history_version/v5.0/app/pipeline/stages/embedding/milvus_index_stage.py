from __future__ import annotations

from app.domain.document import DocumentChunk
from app.infrastructure.milvus.client import VectorRecord, insert_vectors
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage


@registry.stage("milvus_indexer")
class MilvusIndexStage(BaseStage[list[DocumentChunk], list[DocumentChunk]]):
    """
    将带 embedding 的 DocumentChunk 写入 Milvus。
    输入：List[DocumentChunk]（embedding 已填充）
    输出：List[DocumentChunk]（透传，供后续 Stage 使用）
    """

    name = "milvus_indexer"
    requires = frozenset({"embedding_dimension"})  # EmbedStage 必须先跑

    async def _execute(
        self,
        ctx: ProcessingContext,
        chunks: list[DocumentChunk],
    ) -> list[DocumentChunk]:
        records = []
        for chunk in chunks:
            if chunk.embedding is None:
                raise ValueError(
                    f"Chunk {chunk.chunk_id} has no embedding — "
                    "MilvusIndexStage must run after EmbedStage"
                )
            records.append(
                VectorRecord(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    tenant_id=chunk.tenant_id,
                    chunk_index=chunk.chunk_index,
                    embedding=chunk.embedding,
                )
            )

        await insert_vectors(records)
        ctx.with_metadata(milvus_indexed=len(records))
        return chunks
