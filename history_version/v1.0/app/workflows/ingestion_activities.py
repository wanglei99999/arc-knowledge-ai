from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.domain.document import DocumentChunk, DocumentStatus, RawFile
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.registry import registry
from app.providers.base import ParsedDocument


@dataclass
class IngestionInput:
    """Temporal Activity 的序列化输入（必须可 JSON 序列化）"""
    tenant_id: str
    document_id: str
    file_path: str
    mime_type: str
    original_filename: str
    task_id: str
    # 租户配置快照（避免 Activity 执行期间配置变更导致不一致）
    ingestion_strategy: str = "standard"
    embedding_provider: str = "openai_embedding"
    chunk_size: int = 512
    chunk_overlap: int = 64


def _make_context(inp: IngestionInput) -> ProcessingContext:
    """从 Activity 输入构造 ProcessingContext"""
    config = TenantConfig(
        tenant_id=inp.tenant_id,
        ingestion_strategy=inp.ingestion_strategy,
        embedding_provider=inp.embedding_provider,
        chunk_size=inp.chunk_size,
        chunk_overlap=inp.chunk_overlap,
    )
    # Phase 0：配额暂时不限制，Phase 3 加 QuotaGuard 时从 DB 读取真实配额
    quota = QuotaSnapshot(
        max_documents=999999,
        max_storage_bytes=999999999999,
        max_api_calls_per_day=999999,
        used_documents=0,
        used_storage_bytes=0,
        used_api_calls_today=0,
    )
    return ProcessingContext.create(
        tenant_id=inp.tenant_id,
        document_id=inp.document_id,
        quota=quota,
        config=config,
        task_id=inp.task_id,
    )


@activity.defn(name="parse_document")
async def parse_activity(inp: IngestionInput) -> dict:
    """
    Activity 1：解析文档，返回可序列化的 ParsedDocument dict
    Temporal 要求 Activity 返回值可 JSON 序列化。
    """
    ctx = _make_context(inp)
    raw_file = RawFile(
        file_path=inp.file_path,
        mime_type=inp.mime_type,
        original_filename=inp.original_filename,
    )

    parser_stage = registry.get_stage("parser")
    parsed: ParsedDocument = await parser_stage.execute(ctx, raw_file)

    return {"text": parsed.text, "title": parsed.title, "metadata": parsed.metadata}


@activity.defn(name="chunk_document")
async def chunk_activity(inp: IngestionInput, parsed_dict: dict) -> list[dict]:
    """
    Activity 2：切片，返回 chunk dict 列表
    """
    ctx = _make_context(inp)
    parsed = ParsedDocument(
        text=parsed_dict["text"],
        title=parsed_dict.get("title"),
        metadata=parsed_dict.get("metadata", {}),
    )

    chunker_stage = registry.get_stage("token_chunker")
    chunks: list[DocumentChunk] = await chunker_stage.execute(ctx, parsed)

    return [
        {
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "tenant_id": c.tenant_id,
            "content": c.content,
            "chunk_index": c.chunk_index,
            "token_count": c.token_count,
            "metadata": c.metadata,
        }
        for c in chunks
    ]


@activity.defn(name="embed_and_index")
async def embed_and_index_activity(inp: IngestionInput, chunk_dicts: list[dict]) -> int:
    """
    Activity 3：向量化 + 写入 PostgreSQL
    返回成功写入的 chunk 数量。
    """
    ctx = _make_context(inp)

    chunks = [
        DocumentChunk(
            chunk_id=d["chunk_id"],
            document_id=d["document_id"],
            tenant_id=d["tenant_id"],
            content=d["content"],
            chunk_index=d["chunk_index"],
            token_count=d["token_count"],
            metadata=d["metadata"],
        )
        for d in chunk_dicts
    ]

    embedder_stage = registry.get_stage("embedder")
    embedded_chunks: list[DocumentChunk] = await embedder_stage.execute(ctx, chunks)

    repo = ChunkRepository()
    await repo.save_chunks(embedded_chunks)
    await repo.update_document_status(
        inp.document_id, inp.tenant_id, DocumentStatus.INDEXED
    )

    return len(embedded_chunks)
