from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from temporalio import activity

from app.config.settings import settings
from app.domain.document import DocumentChunk, DocumentStatus, RawFile
from app.infrastructure.minio.client import download_file_to_path
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.pipeline import Pipeline
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
    space_id: str = "default"
    # 租户配置快照（避免 Activity 执行期间配置变更导致不一致）
    ingestion_strategy: str = "standard"
    embedding_provider: str = "openai_embedding"
    chunk_size: int = 512
    chunk_overlap: int = 64
    parser_provider: str = "mineru_parser"

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
    Activity 1：从 MinIO 流式下载到临时文件，再解析。

    流式下载确保大文件（200MB+）不会撑爆内存。
    临时文件在 finally 块中清理，无论成败都不残留。
    heartbeat 防止 Temporal 因长时间无响应判定 Activity 超时。
    """
    ctx = _make_context(inp)

    suffix = "." + inp.file_path.rsplit(".", 1)[-1] if "." in inp.file_path else ".bin"
    tmp = tempfile.NamedTemporaryFile(
        suffix=suffix,
        dir=settings.temp_dir,
        delete=False,
    )
    tmp.close()

    try:
        # 流式下载：内存只占一个 chunk（1MB），不受文件大小影响
        await download_file_to_path(inp.file_path, tmp.name)
        activity.heartbeat("downloaded")

        raw_file = RawFile(
            file_path=tmp.name,
            mime_type=inp.mime_type,
            original_filename=inp.original_filename,
        )
        parser_stage = registry.get_stage("parser")
        parsed: ParsedDocument = await parser_stage.execute(ctx, raw_file)
        activity.heartbeat("parsed")

        #空文本 Guard
        if not parsed.text.strip():
            repo = ChunkRepository()
            await repo.update_document_status(
                inp.document_id,
                inp.tenant_id,
                DocumentStatus.FAILED,
                error_message="文档解析结果为空，可能为损坏文件或不支持的扫描格式",
            )
            from temporalio.exceptions import ApplicationError
            raise ApplicationError(
                "empty parse result",
                non_retryable=True,   # 不重试，直接终止 Workflow
            )

    finally:
        os.unlink(tmp.name)

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
            "space_id": inp.space_id,
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
    Activity 3：向量化 + 写入 PostgreSQL + 写入 Milvus。
    走 Strategy → Pipeline，EmbedStage → MilvusIndexStage 顺序执行。
    返回成功写入的 chunk 数量。
    """
    ctx = _make_context(inp)

    chunks = [
        DocumentChunk(
            chunk_id=d["chunk_id"],
            document_id=d["document_id"],
            tenant_id=d["tenant_id"],
            space_id=d["space_id"],
            content=d["content"],
            chunk_index=d["chunk_index"],
            token_count=d["token_count"],
            metadata=d["metadata"],
        )
        for d in chunk_dicts
    ]

    # EmbedStage → MilvusIndexStage → ESIndexStage
    # Activity 3 只负责向量化和写库，不重跑解析/切片
    embed_pipeline = (
        Pipeline.start(registry.get_stage("embedder"))
        .then(registry.get_stage("milvus_indexer"))
        .then(registry.get_stage("es_indexer"))
    )

    try:
        embedded_chunks: list[DocumentChunk] = await embed_pipeline.run(ctx, chunks)

        repo = ChunkRepository()
        await repo.save_chunks(embedded_chunks)
        await repo.update_chunk_count(inp.document_id, inp.tenant_id, len(embedded_chunks))
        await repo.update_document_status(
            inp.document_id, inp.tenant_id, DocumentStatus.INDEXED
        )
    except Exception as exc:
        # gRPC（pymilvus）和其他底层库抛出的异常带有极深的 __cause__ 链，
        # Temporal SDK 递归序列化该链时会撞上 Python 的 recursion limit，
        # 导致真正的错误被 "Failed building exception result" 吞掉。
        # 用 from None 截断链，保留错误类型和消息，让 Temporal 能正常上报。
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from None

    return len(embedded_chunks)
