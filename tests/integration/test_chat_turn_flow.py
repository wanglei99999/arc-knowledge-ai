from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_chat_turn_flow_recovers_attachments_answer_and_citations(
    chat_turn_test_app,
) -> None:
    client = chat_turn_test_app.client
    backend = chat_turn_test_app.backend

    created = await client.post(
        "/chat/turns",
        json={
            "client_request_id": "request-1",
            "session_id": "session-1",
            "query": "合同金额是多少？",
            "attachments": [
                {
                    "client_id": "client-1",
                    "file_name": "contract.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 8,
                }
            ],
        },
    )
    assert created.status_code == 201
    assert created.json()["attachments"][0]["status"] == "pending_upload"

    uploaded = await client.put(
        "/chat/turns/turn-1/attachments/attachment-1",
        files={"file": ("contract.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert uploaded.status_code == 202
    assert uploaded.json()["document_id"] == "document-1"

    pending = await client.get("/chat/turns/turn-1")
    assert pending.json()["readiness"] == "ingesting"
    assert pending.json()["attachments"][0]["status"] == "ingesting"

    backend.mark_indexed()
    ready = await client.get("/chat/turns/turn-1")
    assert ready.json()["readiness"] == "ready"
    assert ready.json()["attachments"][0]["status"] == "indexed"

    answered = await client.post("/chat/turns/turn-1/answer")
    assert answered.status_code == 200
    events = [
        line.removeprefix("data: ")
        for line in answered.text.splitlines()
        if line.startswith("data: ")
    ]
    assert json.loads(events[0]) == {"delta": "金额为 100 万元。"}
    assert json.loads(events[1])["citations"][0]["doc_id"] == "document-1"
    assert events[2] == "[DONE]"
    assert backend.scoped_document_ids == [["document-1"]]

    completed = await client.get("/chat/turns/turn-1")
    assert completed.json()["processing_status"] == "completed"
    assert completed.json()["assistant"]["message_id"] == "answer-1"

    history = await client.get("/sessions/session-1/messages")
    assert history.status_code == 200
    payload = history.json()
    assert payload[0]["processing_status"] == "completed"
    assert payload[0]["attachments"][0]["status"] == "indexed"
    assert payload[0]["citations"] == []
    assert payload[1]["role"] == "assistant"
    assert payload[1]["attachments"] == []
    assert payload[1]["citations"][0]["doc_id"] == "document-1"
