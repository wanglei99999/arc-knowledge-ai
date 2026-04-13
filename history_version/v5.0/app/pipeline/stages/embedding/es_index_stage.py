from __future__ import annotations

from app.domain.document import DocumentChunk
from app.infrastructure.elasticsearch.client import index_chunks
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage


@registry.stage("es_indexer")
class ESIndexStage(BaseStage[list[DocumentChunk], list[DocumentChunk]]):
    """将 chunks 写入 Elasticsearch，供 BM25 全文检索。"""

    name = "es_indexer"

    async def _execute(
        self,
        ctx: ProcessingContext,
        chunks: list[DocumentChunk],
    ) -> list[DocumentChunk]:
        records = [
            {
                "chunk_id":    c.chunk_id,
                "document_id": c.document_id,
                "tenant_id":   c.tenant_id,
                "chunk_index": c.chunk_index,
                "content":     c.content,
            }
            for c in chunks
        ]
        await index_chunks(records)
        return chunks
