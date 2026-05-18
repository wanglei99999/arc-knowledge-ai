from __future__ import annotations

import uuid
from dataclasses import dataclass

from temporalio.client import Client

from app.config.settings import settings
from app.domain.document import DocumentStatus
from app.infrastructure.elasticsearch.client import delete_by_document as es_delete_by_document
from app.infrastructure.minio.client import delete_file as minio_delete_file
from app.infrastructure.milvus.client import delete_by_document as milvus_delete_by_document
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.infrastructure.redis.semantic_cache import cache as _cache
from app.workflows.ingestion_activities import IngestionInput
from app.workflows.ingestion_workflow import IngestionWorkflow


@dataclass
class IngestRequest:
    tenant_id: str
    space_id: str
    file_path: str       # MinIO object key
    mime_type: str
    original_filename: str
    document_id: str | None = None  # API 层上传 MinIO 后传入，None 时自动生成
    # 租户配置（由控制面传入，或从 Nacos 查询）
    ingestion_strategy: str = "standard"
    embedding_provider: str = "openai_embedding"
    chunk_size: int = 512
    chunk_overlap: int = 64


@dataclass
class IngestResult:
    document_id: str
    task_id: str
    workflow_run_id: str


@dataclass
class DeleteResult:
    document_id: str
    message: str = "Document deleted successfully"


class DocumentService:
    """
    文档入库业务逻辑。

    职责：
    1. 生成 document_id
    2. 构造 IngestionInput
    3. 触发 Temporal Workflow（异步，立即返回）
    4. 返回 document_id + task_id 给调用方轮询状态
    """

    async def _get_temporal_client(self) -> Client:
        return await Client.connect(settings.temporal_host)

    async def ingest(self, req: IngestRequest) -> IngestResult:
        document_id = req.document_id or str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        inp = IngestionInput(
            tenant_id=req.tenant_id,
            document_id=document_id,
            file_path=req.file_path,
            mime_type=req.mime_type,
            original_filename=req.original_filename,
            task_id=task_id,
            space_id=req.space_id,
            ingestion_strategy=req.ingestion_strategy,
            embedding_provider=req.embedding_provider,
            chunk_size=req.chunk_size,
            chunk_overlap=req.chunk_overlap,
        )

        client = await self._get_temporal_client()
        handle = await client.start_workflow(
            IngestionWorkflow.run,
            inp,
            id=f"ingest-{document_id}",
            task_queue=settings.temporal_task_queue,
        )

        return IngestResult(
            document_id=document_id,
            task_id=task_id,
            workflow_run_id=handle.run_id,
        )

    async def get_status(self, document_id: str) -> dict:
        """查询 Workflow 执行状态"""
        client = await self._get_temporal_client()
        handle = client.get_workflow_handle(f"ingest-{document_id}")
        desc = await handle.describe()
        return {
            "document_id": document_id,
            "workflow_status": desc.status.name,
        }

    async def list_documents(
        self,
        tenant_id: str,
        space_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        repo = ChunkRepository()
        documents = await repo.list_documents(tenant_id, space_id, limit, offset)
        total = await repo.count_documents(tenant_id, space_id)
        return {"items": documents, "total": total}

    async def delete(self, document_id: str, tenant_id: str) -> DeleteResult:
        """
        删除文档：
        - documents 表逻辑删除（status=DELETED）
        - MinIO / Milvus / ES / PG chunks 物理删除
        """
        repo = ChunkRepository()

        meta = await repo.get_document_meta(document_id, tenant_id)
        if meta is None:
            raise ValueError(f"Document {document_id} not found")

        if meta["status"] in (DocumentStatus.DELETING.value, DocumentStatus.DELETED.value):
            raise ValueError(f"Document {document_id} is already being deleted or deleted")

        # 标记为删除中，防止并发重复删除
        await repo.update_document_status(document_id, tenant_id, DocumentStatus.DELETING)

        # 依次物理删除各存储层（任一失败均向上抛出，由调用方决定是否重试）
        await minio_delete_file(meta["file_path"])
        await milvus_delete_by_document(document_id, tenant_id)
        await es_delete_by_document(document_id, tenant_id)
        await repo.delete_chunks_by_document(document_id, tenant_id)

        # 最后将 documents 记录逻辑删除
        await repo.update_document_status(document_id, tenant_id, DocumentStatus.DELETED)

        # chunk_ids 已物理删除，清除该租户的语义缓存（避免 citations 引用断裂）
        await _cache.invalidate(tenant_id)

        return DeleteResult(document_id=document_id)
