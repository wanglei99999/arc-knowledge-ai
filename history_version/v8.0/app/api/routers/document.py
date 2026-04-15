from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.infrastructure.minio.client import build_object_key, upload_file
from app.services.document_service import DeleteResult, DocumentService, IngestRequest

router = APIRouter(prefix="/documents", tags=["documents"])

_service = DocumentService()


class UploadResponse(BaseModel):
    document_id: str
    task_id: str
    message: str = "Document ingestion started"


class StatusResponse(BaseModel):
    document_id: str
    workflow_status: str


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="上传文档并触发入库",
)
async def upload_document(
    file: UploadFile,
    space_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> UploadResponse:
    """
    接收文件上传，写入 MinIO（Phase 1 实现），触发 Temporal Workflow。
    立即返回 document_id，前端通过 GET /documents/{id}/status 轮询进度。
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    mime_type = file.content_type or "application/octet-stream"

    # Phase 1：上传到 MinIO，用 document_id 作为 object key 避免文件名冲突
    import uuid
    document_id = str(uuid.uuid4())
    data = await file.read()
    object_key = build_object_key(x_tenant_id, space_id, document_id, file.filename)
    await upload_file(data, object_key, content_type=mime_type)

    req = IngestRequest(
        tenant_id=x_tenant_id,
        space_id=space_id,
        file_path=object_key,
        mime_type=mime_type,
        original_filename=file.filename,
        document_id=document_id,
    )

    result = await _service.ingest(req)

    return UploadResponse(
        document_id=result.document_id,
        task_id=result.task_id,
    )


@router.get(
    "/{document_id}/status",
    response_model=StatusResponse,
    summary="查询文档处理状态",
)
async def get_document_status(
    document_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> StatusResponse:
    data = await _service.get_status(document_id)
    return StatusResponse(**data)


@router.get(
    "",
    summary="查询文档列表",
)
async def list_documents(
    space_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> dict:
    return await _service.list_documents(x_tenant_id, space_id, limit, offset)


@router.get(
    "/{document_id}/chunks",
    summary="查询文档切片列表",
)
async def list_chunks(
    document_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> dict:
    from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
    repo = ChunkRepository()
    chunks = await repo.get_chunks_by_document(document_id, x_tenant_id)
    return {"items": chunks, "total": len(chunks)}


@router.delete(
    "/{document_id}",
    response_model=DeleteResult,
    status_code=status.HTTP_200_OK,
    summary="删除文档及其所有索引数据",
)
async def delete_document(
    document_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> DeleteResult:
    """
    删除指定文档：
    - documents 记录逻辑删除（status=DELETED）
    - MinIO 原始文件、Milvus 向量、ES 索引、PG chunks 全部物理删除
    """
    try:
        return await _service.delete(document_id, x_tenant_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND
            if "not found" in str(e)
            else status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
