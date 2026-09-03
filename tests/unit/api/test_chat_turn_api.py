from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import UserContext, require_user
from app.api.routers import chat_turn
from app.config.settings import settings
from app.domain.chat_turn import (
    AttachmentStatus,
    AttachmentView,
    ChatTurnView,
    TurnProcessingStatus,
    TurnReadiness,
)
from app.services.chat_turn_service import (
    AttachmentUploadResult,
    ChatTurnConflictError,
    ChatTurnNotFoundError,
)
from app.services.document_service import IngestResult


def _turn() -> ChatTurnView:
    return ChatTurnView(
        turn_id="turn-1",
        session_id="session-1",
        space_id="space-1",
        query="总结附件",
        readiness=TurnReadiness.INGESTING,
        processing_status=TurnProcessingStatus.WAITING_FILES,
        processing_error=None,
        attachments=[
            AttachmentView(
                attachment_id="attachment-1",
                client_id="client-1",
                file_name="contract.pdf",
                mime_type="application/pdf",
                file_size=8,
                document_id=None,
                status=AttachmentStatus.PENDING_UPLOAD,
                ignored=False,
                error_message=None,
            )
        ],
        assistant=None,
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error: Exception | None = None

    async def _result(self, name: str, kwargs: dict, result):
        self.calls.append((name, kwargs))
        if self.error:
            raise self.error
        return result

    async def create_turn(self, **kwargs):
        return await self._result("create", kwargs, _turn())

    async def upload_attachment(self, **kwargs):
        return await self._result(
            "upload", kwargs, AttachmentUploadResult("document-1", "task-1", "run-1")
        )

    async def get_turn(self, **kwargs):
        return await self._result("get", kwargs, _turn())

    async def add_attachment(self, **kwargs):
        return await self._result("add", kwargs, _turn())

    async def retry_attachment(self, **kwargs):
        return await self._result("retry", kwargs, IngestResult("document-1", "task-2", "run-2"))

    async def set_ignored(self, **kwargs):
        turn = replace(_turn(), readiness=TurnReadiness.EMPTY)
        return await self._result("ignore", kwargs, turn)

    async def cancel_turn(self, **kwargs):
        turn = replace(_turn(), processing_status=TurnProcessingStatus.CANCELLED)
        return await self._result("cancel", kwargs, turn)

    async def prepare_answer(self, **kwargs):
        return await self._result(
            "prepare_answer",
            kwargs,
            SimpleNamespace(
                turn_id="turn-1",
                session_id="session-1",
                space_id="space-1",
                query="总结附件",
                document_ids=["document-1"],
            ),
        )

    async def stream_answer(self, claim):
        self.calls.append(("stream_answer", {"claim": claim}))
        yield "回答"
        yield [{"doc_id": "document-1"}]


@pytest.fixture
def api(monkeypatch):
    service = _Service()
    monkeypatch.setattr(chat_turn, "_service", service)
    app = FastAPI()
    app.include_router(chat_turn.router)
    app.dependency_overrides[require_user] = lambda: UserContext(
        tenant_id="tenant-1", user_id="user-1"
    )
    return TestClient(app, raise_server_exceptions=False), service


def _declaration() -> dict:
    return {
        "client_id": "client-1",
        "file_name": "contract.pdf",
        "mime_type": "application/pdf",
        "file_size": 8,
    }


def test_create_turn_returns_persisted_attachment_state(api) -> None:
    client, service = api

    response = client.post(
        "/chat/turns",
        json={
            "client_request_id": "11111111-1111-4111-8111-111111111111",
            "session_id": "session-1",
            "query": "总结附件",
            "attachments": [_declaration()],
        },
    )

    assert response.status_code == 201
    assert response.json()["turn_id"] == "turn-1"
    assert response.json()["attachments"][0]["status"] == "pending_upload"
    _, kwargs = service.calls[0]
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["user_id"] == "user-1"
    assert kwargs["attachments"][0].file_name == "contract.pdf"


def test_upload_attachment_uses_multipart_bytes_and_returns_202(api) -> None:
    client, service = api

    response = client.put(
        "/chat/turns/turn-1/attachments/attachment-1",
        files={"file": ("contract.pdf", b"%PDF-1.7", "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json() == {
        "document_id": "document-1",
        "task_id": "task-1",
        "workflow_run_id": "run-1",
    }
    _, kwargs = service.calls[0]
    assert kwargs["data"] == b"%PDF-1.7"
    assert kwargs["mime_type"] == "application/pdf"


@pytest.mark.parametrize(
    "method,path,json_body,expected_call",
    [
        ("get", "/chat/turns/turn-1", None, "get"),
        ("post", "/chat/turns/turn-1/attachments", _declaration(), "add"),
        (
            "post",
            "/chat/turns/turn-1/attachments/attachment-1/retry",
            None,
            "retry",
        ),
        (
            "patch",
            "/chat/turns/turn-1/attachments/attachment-1",
            {"ignored": True},
            "ignore",
        ),
        ("post", "/chat/turns/turn-1/cancel", None, "cancel"),
    ],
)
def test_turn_control_endpoints_call_service(api, method, path, json_body, expected_call):
    client, service = api
    kwargs = {"json": json_body} if json_body is not None else {}

    response = client.request(method, path, **kwargs)

    assert response.status_code == 200
    assert service.calls[0][0] == expected_call
    assert service.calls[0][1]["tenant_id"] == "tenant-1"
    assert service.calls[0][1]["user_id"] == "user-1"


@pytest.mark.parametrize(
    "error,status_code",
    [
        (ChatTurnNotFoundError("不存在"), 404),
        (ChatTurnConflictError("状态冲突"), 409),
    ],
)
def test_business_errors_have_stable_http_status(api, error, status_code) -> None:
    client, service = api
    service.error = error

    response = client.get("/chat/turns/turn-1")

    assert response.status_code == status_code
    assert response.json()["detail"] == str(error)


def test_turn_endpoints_require_authenticated_user_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    app = FastAPI()
    app.include_router(chat_turn.router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/chat/turns/turn-1",
        headers={"X-Tenant-Id": "forged-tenant"},
    )

    assert response.status_code == 401


def test_answer_endpoint_prepares_before_streaming_and_uses_existing_sse_shape(
    api,
) -> None:
    client, service = api

    response = client.post("/chat/turns/turn-1/answer")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.splitlines() == [
        'data: {"delta": "回答"}',
        "",
        'data: {"citations": [{"doc_id": "document-1"}]}',
        "",
        "data: [DONE]",
        "",
    ]
    assert [name for name, _ in service.calls] == [
        "prepare_answer",
        "stream_answer",
    ]


def test_answer_conflict_returns_http_409_before_sse_starts(api) -> None:
    client, service = api
    service.error = ChatTurnConflictError("当前聊天轮次正在回答")

    response = client.post("/chat/turns/turn-1/answer")

    assert response.status_code == 409
    assert response.json()["detail"] == "当前聊天轮次正在回答"
    assert response.headers["content-type"].startswith("application/json")
