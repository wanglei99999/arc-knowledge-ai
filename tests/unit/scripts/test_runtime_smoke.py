import json

import httpx
import pytest

from scripts.runtime import smoke
from scripts.runtime.smoke import (
    SmokeConfig,
    SmokeFailure,
    parse_sse,
    run_l0,
    run_l1,
    search_payload_contains,
    wait_for_document,
)


def test_parse_sse_collects_answer_and_citations():
    events = parse_sse(
        [
            'data: {"delta":"ORCHID-7429"}',
            "",
            'data: {"citations":[{"doc_id":"d1"}]}',
            "",
            "data: [DONE]",
            "",
        ]
    )

    assert events.answer == "ORCHID-7429"
    assert events.citations[0]["doc_id"] == "d1"


def test_parse_sse_rejects_an_error_event():
    with pytest.raises(SmokeFailure, match="model unavailable"):
        parse_sse(['data: {"error":"model unavailable"}'])


class ResponseStub:
    status_code = 200

    def json(self):
        return {"workflow_status": "FAILED"}

    def raise_for_status(self):
        return None


class ClientStub:
    async def get(self, path):
        return ResponseStub()


@pytest.mark.asyncio
async def test_wait_for_document_rejects_failed_workflow():
    with pytest.raises(SmokeFailure, match="FAILED"):
        await wait_for_document(ClientStub(), "d1", timeout_seconds=1)


def test_search_payload_checks_hits_and_chunks():
    assert search_payload_contains(
        {
            "hits": [{"content": "not here"}],
            "chunks": [{"content": "The codeword is ORCHID-7429."}],
        },
        "ORCHID-7429",
    )
    assert not search_payload_contains({"hits": [], "chunks": []}, "ORCHID-7429")


def smoke_config() -> SmokeConfig:
    return SmokeConfig(
        api_base_url="http://api:8000",
        web_base_url="http://web",
        tenant_id="r0-smoke",
        email="r0-smoke@local.invalid",
        password="never-print-this-password",
        timeout_seconds=5,
    )


@pytest.mark.asyncio
async def test_run_l0_accepts_degraded_readiness_when_required_checks_pass():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "web":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html>Incipit</html>",
            )
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/ready":
            return httpx.Response(
                200,
                json={
                    "status": "degraded",
                    "checks": [
                        {"name": "postgres", "required": True, "status": "ok"},
                        {"name": "llm", "required": False, "status": "failed"},
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async def schema_probe():
        return {"name": "schema", "ok": True, "detail": "all tables present"}

    async def worker_probe():
        return {"name": "worker", "ok": True, "detail": "1 workflow poller"}

    result = await run_l0(
        smoke_config(),
        transport=httpx.MockTransport(handler),
        schema_probe=schema_probe,
        worker_probe=worker_probe,
    )

    assert result["status"] == "PASS"
    ready = next(check for check in result["checks"] if check["name"] == "api-ready")
    assert ready["state"] == "WARN"
    assert ready["detail"] == "degraded"
    assert all(check["elapsed_ms"] >= 0 for check in result["checks"])


@pytest.mark.asyncio
async def test_run_l0_labels_schema_execution_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "web":
            return httpx.Response(200, headers={"content-type": "text/html"})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"status": "healthy", "checks": []})

    async def failed_schema_probe():
        raise ConnectionError("PostgreSQL refused the connection")

    async def worker_probe():
        return {"name": "worker", "ok": True, "detail": "1 workflow poller"}

    with pytest.raises(SmokeFailure, match="PostgreSQL refused") as error:
        await run_l0(
            smoke_config(),
            transport=httpx.MockTransport(handler),
            schema_probe=failed_schema_probe,
            worker_probe=worker_probe,
        )

    assert error.value.stage == "schema"


@pytest.mark.asyncio
async def test_run_l1_executes_business_flow_and_cleans_only_created_resources():
    requested: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append((request.method, request.url.path))
        if request.url.path == "/auth/register":
            return httpx.Response(409, json={"detail": "already registered"})
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"access_token": "token"})
        if request.url.path == "/spaces" and request.method == "POST":
            assert request.headers["authorization"] == "Bearer token"
            return httpx.Response(201, json={"space_id": "space-1"})
        if request.url.path == "/documents/upload":
            assert request.url.params["space_id"] == "space-1"
            assert b'name="file"' in request.content
            return httpx.Response(202, json={"document_id": "doc-1"})
        if request.url.path == "/documents/doc-1/status":
            return httpx.Response(200, json={"workflow_status": "COMPLETED"})
        if request.url.path == "/search":
            assert request.url.params["q"] == "ORCHID-7429"
            assert request.url.params["space_id"] == "space-1"
            assert request.url.params["score_threshold"] == "0"
            return httpx.Response(
                200,
                json={
                    "hits": [],
                    "chunks": [{"content": "The codeword is ORCHID-7429."}],
                },
            )
        if request.url.path == "/sessions":
            return httpx.Response(201, json={"session_id": "session-1"})
        if request.url.path == "/chat":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    'data: {"delta":"ORCHID-7429"}\n\n'
                    'data: {"citations":[{"doc_id":"doc-1"}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            )
        if request.url.path == "/documents/doc-1" and request.method == "DELETE":
            return httpx.Response(500, json={"detail": "cleanup unavailable"})
        if request.url.path == "/spaces/space-1" and request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def successful_l0(config, **kwargs):
        return {
            "level": "L0",
            "status": "PASS",
            "checks": [
                {"name": "dependency:llm", "state": "PASS"},
                {"name": "dependency:embedding", "state": "PASS"},
            ],
        }

    result = await run_l1(
        smoke_config(),
        transport=httpx.MockTransport(handler),
        l0_runner=successful_l0,
    )

    assert result["status"] == "PASS"
    assert result["resources"] == {
        "space_id": "space-1",
        "document_id": "doc-1",
        "session_id": "session-1",
    }
    assert ("DELETE", "/documents/doc-1") in requested
    assert ("DELETE", "/spaces/space-1") in requested
    assert all(path != "/admin/dev/reset" for _, path in requested)
    assert result["warnings"] == ["cleanup document doc-1 returned HTTP 500"]


@pytest.mark.asyncio
async def test_run_l1_rejects_unhealthy_models_before_creating_resources():
    async def degraded_l0(config, **kwargs):
        return {
            "level": "L0",
            "status": "PASS",
            "checks": [
                {"name": "dependency:llm", "state": "WARN"},
                {"name": "dependency:embedding", "state": "PASS"},
            ],
        }

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"L1 should not create resources: {request.url}")

    with pytest.raises(SmokeFailure, match="dependency:llm") as error:
        await run_l1(
            smoke_config(),
            transport=httpx.MockTransport(unexpected_request),
            l0_runner=degraded_l0,
        )

    assert error.value.stage == "l1-prerequisites"
    assert error.value.resources == {}


@pytest.mark.asyncio
async def test_cli_failure_reports_stage_resources_and_trace_without_password(
    monkeypatch,
    capsys,
):
    async def failed_run(config, level):
        raise SmokeFailure(
            "chat returned HTTP 500",
            stage="chat",
            resources={"space_id": "space-1"},
            trace_id="trace-123",
        )

    monkeypatch.setenv("SMOKE_PASSWORD", "never-print-this-password")
    monkeypatch.setattr(smoke, "run_smoke", failed_run)

    exit_code = await smoke.main(["--level", "l1"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 1
    assert payload["stage"] == "chat"
    assert payload["resources"] == {"space_id": "space-1"}
    assert payload["trace_id"] == "trace-123"
    assert "never-print-this-password" not in output
