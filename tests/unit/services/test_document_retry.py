from __future__ import annotations

import pytest

import app.services.document_service as service_module
from app.domain.document import DocumentStatus
from app.services.document_service import DocumentService, IngestRequest


@pytest.mark.asyncio
async def test_retry_cleans_partial_indexes_before_new_workflow(monkeypatch) -> None:
    events: list[str] = []

    class _Repo:
        async def get_document_meta(self, document_id, tenant_id):
            return {
                "document_id": document_id,
                "tenant_id": tenant_id,
                "space_id": "space-1",
                "file_path": "tenant/space/document.pdf",
                "original_name": "contract.pdf",
                "mime_type": "application/pdf",
                "status": "failed",
            }

        async def delete_chunks_by_document(self, document_id, tenant_id):
            events.append("postgres")

        async def reset_failed_document(self, document_id, tenant_id):
            events.append("reset")
            return True

        async def update_document_status(self, *args, **kwargs):
            events.append("failed-again")

    async def delete_milvus(document_id, tenant_id):
        events.append("milvus")

    async def delete_es(document_id, tenant_id):
        events.append("elasticsearch")

    class _Handle:
        run_id = "retry-run-1"

    class _Client:
        async def start_workflow(self, workflow, inp, *, id, task_queue):
            events.append("workflow")
            assert id.startswith("ingest-document-1-retry-")
            assert inp.file_path == "tenant/space/document.pdf"
            assert inp.original_filename == "contract.pdf"
            assert inp.space_id == "space-1"
            return _Handle()

    monkeypatch.setattr(service_module, "ChunkRepository", _Repo)
    monkeypatch.setattr(service_module, "milvus_delete_by_document", delete_milvus)
    monkeypatch.setattr(service_module, "es_delete_by_document", delete_es)
    monkeypatch.setattr(
        DocumentService,
        "_get_temporal_client",
        lambda self: _return(_Client()),
    )

    result = await DocumentService().retry_ingestion("document-1", "tenant-1")

    assert events == ["reset", "milvus", "elasticsearch", "postgres", "workflow"]
    assert result.document_id == "document-1"
    assert result.workflow_run_id == "retry-run-1"


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_document_before_cleanup(monkeypatch) -> None:
    class _Repo:
        async def get_document_meta(self, document_id, tenant_id):
            return {"document_id": document_id, "status": "indexed"}

    monkeypatch.setattr(service_module, "ChunkRepository", _Repo)

    with pytest.raises(ValueError, match="failed"):
        await DocumentService().retry_ingestion("document-1", "tenant-1")


@pytest.mark.asyncio
async def test_retry_loser_does_not_clean_indexes(monkeypatch) -> None:
    class _Repo:
        async def get_document_meta(self, document_id, tenant_id):
            return {"document_id": document_id, "status": "failed"}

        async def reset_failed_document(self, document_id, tenant_id):
            return False

    async def unexpected_cleanup(*args):
        raise AssertionError("没有抢到重试权的请求不能清理索引")

    monkeypatch.setattr(service_module, "ChunkRepository", _Repo)
    monkeypatch.setattr(service_module, "milvus_delete_by_document", unexpected_cleanup)
    monkeypatch.setattr(service_module, "es_delete_by_document", unexpected_cleanup)

    with pytest.raises(ValueError, match="already being retried"):
        await DocumentService().retry_ingestion("document-1", "tenant-1")


@pytest.mark.asyncio
async def test_ingest_marks_document_failed_when_workflow_cannot_start(
    monkeypatch,
) -> None:
    status_updates = []

    class _Repo:
        async def update_document_status(self, *args, **kwargs):
            status_updates.append((args, kwargs))

    class _Client:
        async def start_workflow(self, *args, **kwargs):
            raise RuntimeError("Temporal unavailable")

    monkeypatch.setattr(service_module, "ChunkRepository", _Repo)
    monkeypatch.setattr(
        DocumentService,
        "_get_temporal_client",
        lambda self: _return(_Client()),
    )

    with pytest.raises(RuntimeError, match="Temporal unavailable"):
        await DocumentService().ingest(
            IngestRequest(
                tenant_id="tenant-1",
                space_id="space-1",
                file_path="tenant/space/document.pdf",
                mime_type="application/pdf",
                original_filename="contract.pdf",
                document_id="document-1",
            )
        )

    args, kwargs = status_updates[0]
    assert args[:3] == ("document-1", "tenant-1", DocumentStatus.FAILED)
    assert kwargs["error_message"] == "入库任务启动失败，请稍后再试"


async def _return(value):
    return value
