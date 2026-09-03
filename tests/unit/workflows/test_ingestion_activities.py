from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.document import DocumentStatus


def test_core_ingestion_uses_the_embedded_parser_by_default() -> None:
    from app.workflows import ingestion_activities as module

    inp = module.IngestionInput(
        tenant_id="tenant-1",
        document_id="document-1",
        file_path="documents/smoke.md",
        mime_type="text/markdown",
        original_filename="smoke.md",
        task_id="task-1",
    )

    assert inp.parser_provider == "unstructured_parser"
    assert module._make_context(inp).config.parser_provider == "unstructured_parser"


@pytest.mark.asyncio
async def test_parse_failure_marks_document_failed(monkeypatch, tmp_path) -> None:
    from app.workflows import ingestion_activities as module

    class FailingParser:
        async def execute(self, _ctx, _raw_file):
            raise ValueError("broken pdf")

    class RecordingRepo:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def update_document_status(
            self,
            document_id,
            tenant_id,
            status,
            *,
            error_message=None,
        ) -> None:
            self.calls.append((document_id, tenant_id, status, error_message))

    async def fake_download(_object_key: str, _target_path: str) -> None:
        return None

    repo = RecordingRepo()
    monkeypatch.setattr(module, "download_file_to_path", fake_download)
    monkeypatch.setattr(module, "ChunkRepository", lambda: repo)
    monkeypatch.setattr(module.registry, "get_stage", lambda _name: FailingParser())
    monkeypatch.setattr(module.activity, "heartbeat", lambda _detail: None)
    monkeypatch.setattr(module, "settings", SimpleNamespace(temp_dir=str(tmp_path)))

    inp = module.IngestionInput(
        tenant_id="tenant-1",
        document_id="document-1",
        file_path="documents/broken.pdf",
        mime_type="application/pdf",
        original_filename="broken.pdf",
        task_id="task-1",
    )

    with pytest.raises(RuntimeError, match="ValueError: broken pdf"):
        await module.parse_activity(inp)

    assert repo.calls == [
        (
            "document-1",
            "tenant-1",
            DocumentStatus.FAILED,
            "文档解析失败：ValueError: broken pdf",
        )
    ]
