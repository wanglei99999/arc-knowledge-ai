from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.dependencies import UserContext, require_user
from app.config.settings import settings
from app.infrastructure.minio.client import build_object_key, upload_file
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.services.authorization_service import authorization
from app.services.document_service import DeleteResult, DocumentService, IngestRequest

router = APIRouter(prefix="/documents", tags=["documents"])

_service = DocumentService()


def _parse_metadata(raw: str | None) -> dict:
    """解析上传时的文档级 metadata（JSON 字符串），非法返回 400。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="metadata 必须是合法 JSON"
        ) from None
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="metadata 必须是 JSON object"
        ) from None
    return data


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
    metadata: str | None = None,
    user: UserContext = Depends(require_user),
) -> UploadResponse:
    """
    接收文件上传，写入 MinIO（Phase 1 实现），触发 Temporal Workflow。
    立即返回 document_id，前端通过 GET /documents/{id}/status 轮询进度。
    """
    tenant_id = user.tenant_id
    await authorization.authorize_user(tenant_id, user.user_id, space_id, "document.upload")
    parsed_metadata = _parse_metadata(metadata)
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    mime_type = file.content_type or "application/octet-stream"

    # 上传到 MinIO
    import uuid

    document_id = str(uuid.uuid4())
    data = await file.read(settings.document_max_file_size_bytes + 1)
    if len(data) > settings.document_max_file_size_bytes:
        raise HTTPException(413, detail="File exceeds upload size limit")
    object_key = build_object_key(tenant_id, space_id, document_id, file.filename)
    await upload_file(data, object_key, content_type=mime_type)

    # 创建 documents 记录（对应架构中 arc-knowledge 控制面的职责，MVP 阶段由 Python API 代劳）
    repo = ChunkRepository()
    await repo.create_document(
        document_id=document_id,
        tenant_id=tenant_id,
        space_id=space_id,
        original_name=file.filename,
        mime_type=mime_type,
        file_path=object_key,
        file_size=len(data),
        created_by_membership_id=user.membership_id,
    )

    # 我们在这里的设计上是，matadata 存在于 chunk 中，而不是落在 document 中，
    # 所以创建 document 的时候是不带 matadata 的
    req = IngestRequest(
        tenant_id=tenant_id,
        user_id=user.user_id,
        space_id=space_id,
        file_path=object_key,
        mime_type=mime_type,
        original_filename=file.filename,
        document_id=document_id,
        metadata=parsed_metadata,
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
    user: UserContext = Depends(require_user),
) -> StatusResponse:
    try:
        data = await _service.get_status(document_id, user.tenant_id, user_id=user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return StatusResponse(**data)


@router.get(
    "",
    summary="查询文档列表",
)
async def list_documents(
    space_id: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: UserContext = Depends(require_user),
) -> dict:
    return await _service.list_documents(
        user.tenant_id, space_id, limit, offset, user_id=user.user_id
    )


@router.get(
    "/{document_id}/chunks",
    summary="查询文档切片列表",
)
async def list_chunks(
    document_id: str,
    user: UserContext = Depends(require_user),
) -> dict:
    from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository

    await authorization.authorize_document(user.tenant_id, user.user_id, document_id)
    repo = ChunkRepository()
    chunks = await repo.get_chunks_by_document(document_id, user.tenant_id)
    return {"items": chunks, "total": len(chunks)}


@router.delete(
    "/{document_id}",
    response_model=DeleteResult,
    status_code=status.HTTP_200_OK,
    summary="删除文档及其所有索引数据",
)
async def delete_document(
    document_id: str,
    user: UserContext = Depends(require_user),
) -> DeleteResult:
    """
    删除指定文档：
    - documents 记录逻辑删除（status=DELETED）
    - MinIO 原始文件、Milvus 向量、ES 索引、PG chunks 全部物理删除
    """
    try:
        return await _service.delete(document_id, user.tenant_id, user_id=user.user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND if "not found" in str(e) else status.HTTP_409_CONFLICT
            ),
            detail=str(e),
        ) from e


@router.post("/{document_id}/retry", status_code=202)
async def retry_document(document_id: str, user: UserContext = Depends(require_user)):
    try:
        return await _service.retry_ingestion(document_id, user.tenant_id, user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{document_id}/download")
async def download_document(document_id: str, user: UserContext = Depends(require_user)):
    data, meta = await _service.get_content(document_id, user.tenant_id, user_id=user.user_id)
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(meta["original_name"], safe=""),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
