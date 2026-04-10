from __future__ import annotations

import uuid
from dataclasses import dataclass

from temporalio.client import Client

from app.config.settings import settings
from app.workflows.ingestion_activities import IngestionInput
from app.workflows.ingestion_workflow import IngestionWorkflow


@dataclass
class IngestRequest:
    tenant_id: str
    space_id: str
    file_path: str       # MinIO 上传后的路径
    mime_type: str
    original_filename: str
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
        document_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        inp = IngestionInput(
            tenant_id=req.tenant_id,
            document_id=document_id,
            file_path=req.file_path,
            mime_type=req.mime_type,
            original_filename=req.original_filename,
            task_id=task_id,
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
