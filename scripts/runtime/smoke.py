from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any, Protocol
from uuid import uuid4

import httpx

from scripts.runtime.runtime_probe import probe_schema, probe_worker

CODEWORD = "ORCHID-7429"
QUESTION = "What is the Incipit R0 verification codeword?"
DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/r0-smoke.md"


class SmokeFailure(RuntimeError):
    """A smoke stage completed with an unacceptable runtime result."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        resources: dict[str, str] | None = None,
        trace_id: str | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.resources = dict(resources or {})
        self.trace_id = trace_id
        self.warnings = list(warnings or [])


@dataclass(frozen=True)
class SmokeConfig:
    api_base_url: str
    web_base_url: str
    tenant_id: str
    email: str
    password: str
    timeout_seconds: int = 300


@dataclass(frozen=True)
class SseResult:
    answer: str
    citations: list[dict[str, object]]


class AsyncDocumentClient(Protocol):
    async def get(self, path: str): ...


Probe = Callable[[], Awaitable[dict[str, object]]]
L0Runner = Callable[..., Awaitable[dict[str, object]]]


def parse_sse(lines: list[str]) -> SseResult:
    answer: list[str] = []
    citations: list[dict[str, object]] = []

    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line.partition(":")[2].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SmokeFailure(f"invalid SSE JSON: {data}") from exc
        if "error" in payload:
            raise SmokeFailure(str(payload["error"]))
        answer.append(str(payload.get("delta", "")))
        citations.extend(payload.get("citations", []))

    return SseResult("".join(answer), citations)


async def wait_for_document(
    client: AsyncDocumentClient,
    document_id: str,
    timeout_seconds: int,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = await client.get(f"/documents/{document_id}/status")
        response.raise_for_status()
        status = response.json()["workflow_status"]
        if status == "COMPLETED":
            return
        if status in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            raise SmokeFailure(f"document workflow ended as {status}")
        await asyncio.sleep(2)

    raise SmokeFailure(f"document {document_id} did not complete in {timeout_seconds}s")


def search_payload_contains(payload: dict[str, object], needle: str) -> bool:
    def content_contains(value: object) -> bool:
        if isinstance(value, dict):
            return any(
                content_contains(item)
                for key, item in value.items()
                if key in {"content", "text", "source"}
            )
        if isinstance(value, list):
            return any(content_contains(item) for item in value)
        return isinstance(value, str) and needle in value

    return any(
        content_contains(item)
        for collection in (payload.get("hits", []), payload.get("chunks", []))
        for item in collection
    )


def _trace_id(response: httpx.Response | None) -> str | None:
    if response is None:
        return None
    for name in ("x-trace-id", "trace-id", "x-request-id", "traceparent"):
        if value := response.headers.get(name):
            return value
    return None


def _http_failure(stage: str, response: httpx.Response) -> SmokeFailure:
    return SmokeFailure(
        f"{stage} returned HTTP {response.status_code}",
        stage=stage,
        trace_id=_trace_id(response),
    )


def _require_status(
    response: httpx.Response,
    stage: str,
    accepted: set[int] = {200},
) -> None:
    if response.status_code not in accepted:
        raise _http_failure(stage, response)


def _check(name: str, state: str, detail: str, started: float) -> dict[str, object]:
    return {
        "name": name,
        "state": state,
        "detail": detail,
        "elapsed_ms": max(0, int((perf_counter() - started) * 1000)),
    }


async def _get_or_fail(
    client: httpx.AsyncClient,
    path: str,
    stage: str,
) -> httpx.Response:
    try:
        return await client.get(path)
    except httpx.HTTPError as exc:
        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
        raise SmokeFailure(
            str(exc) or type(exc).__name__,
            stage=stage,
            trace_id=_trace_id(response),
        ) from exc


async def run_l0(
    config: SmokeConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    schema_probe: Probe | None = None,
    worker_probe: Probe | None = None,
) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    timeout = httpx.Timeout(config.timeout_seconds)

    async with (
        httpx.AsyncClient(
            base_url=config.api_base_url,
            timeout=timeout,
            transport=transport,
        ) as api,
        httpx.AsyncClient(
            base_url=config.web_base_url,
            timeout=timeout,
            transport=transport,
        ) as web,
    ):
        started = perf_counter()
        health = await _get_or_fail(api, "/health", "api-health")
        _require_status(health, "api-health")
        try:
            health_payload = health.json()
        except ValueError as exc:
            raise SmokeFailure("api-health returned invalid JSON", stage="api-health") from exc
        if health_payload.get("status") != "ok":
            raise SmokeFailure("api-health status is not ok", stage="api-health")
        checks.append(_check("api-health", "PASS", "HTTP 200, status=ok", started))

        started = perf_counter()
        ready = await _get_or_fail(api, "/ready", "api-ready")
        _require_status(ready, "api-ready")
        try:
            ready_payload = ready.json()
        except ValueError as exc:
            raise SmokeFailure("api-ready returned invalid JSON", stage="api-ready") from exc
        ready_state = ready_payload.get("status")
        if ready_state not in {"healthy", "degraded"}:
            raise SmokeFailure(
                f"api-ready reported {ready_state}",
                stage="api-ready",
                trace_id=_trace_id(ready),
            )
        required_failures = [
            item.get("name", "unknown")
            for item in ready_payload.get("checks", [])
            if item.get("required") and item.get("status") != "ok"
        ]
        if required_failures:
            raise SmokeFailure(
                f"api-ready required checks failed: {', '.join(required_failures)}",
                stage="api-ready",
                trace_id=_trace_id(ready),
            )
        checks.append(
            _check(
                "api-ready",
                "WARN" if ready_state == "degraded" else "PASS",
                str(ready_state),
                started,
            )
        )
        for item in ready_payload.get("checks", []):
            item_state = "PASS" if item.get("status") == "ok" else "WARN"
            checks.append(
                _check(
                    f"dependency:{item.get('name', 'unknown')}",
                    item_state,
                    str(item.get("detail", item.get("status", "unknown"))),
                    started,
                )
            )

        started = perf_counter()
        web_response = await _get_or_fail(web, "/", "web")
        _require_status(web_response, "web")
        content_type = web_response.headers.get("content-type", "").lower()
        if "text/html" not in content_type:
            raise SmokeFailure(
                f"web returned non-HTML content type: {content_type or 'missing'}",
                stage="web",
                trace_id=_trace_id(web_response),
            )
        checks.append(_check("web", "PASS", "HTTP 200, text/html", started))

    started = perf_counter()
    try:
        schema_result = await (schema_probe or probe_schema)()
    except Exception as exc:
        raise _as_smoke_failure(exc, "schema") from exc
    if not schema_result.get("ok"):
        raise SmokeFailure(str(schema_result.get("detail")), stage="schema")
    checks.append(_check("schema", "PASS", str(schema_result.get("detail", "ok")), started))

    started = perf_counter()
    try:
        worker_result = await (worker_probe or probe_worker)()
    except Exception as exc:
        raise _as_smoke_failure(exc, "worker") from exc
    if not worker_result.get("ok"):
        raise SmokeFailure(str(worker_result.get("detail")), stage="worker")
    checks.append(_check("worker", "PASS", str(worker_result.get("detail", "ok")), started))

    return {"level": "L0", "status": "PASS", "checks": checks}


async def _cleanup_resource(
    client: httpx.AsyncClient,
    path: str,
    name: str,
    warnings: list[str],
) -> None:
    try:
        response = await client.delete(path)
        if not response.is_success:
            warnings.append(f"cleanup {name} returned HTTP {response.status_code}")
    except Exception as exc:
        warnings.append(f"cleanup {name} failed: {exc}")


def _as_smoke_failure(exc: Exception, stage: str) -> SmokeFailure:
    if isinstance(exc, SmokeFailure):
        if exc.stage is None:
            exc.stage = stage
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        return SmokeFailure(
            f"{stage} returned HTTP {exc.response.status_code}",
            stage=stage,
            trace_id=_trace_id(exc.response),
        )
    return SmokeFailure(str(exc) or type(exc).__name__, stage=stage)


async def run_l1(
    config: SmokeConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    l0_runner: L0Runner | None = None,
    fixture_path: Path = DEFAULT_FIXTURE,
) -> dict[str, object]:
    try:
        l0_result = await (l0_runner or run_l0)(config, transport=transport)
    except Exception as exc:
        raise _as_smoke_failure(exc, "l0") from exc
    checks = list(l0_result.get("checks", []))
    strict_dependencies = {"dependency:llm", "dependency:embedding"}
    failed_strict_dependencies = [
        str(item.get("name"))
        for item in checks
        if item.get("name") in strict_dependencies and item.get("state") != "PASS"
    ]
    if failed_strict_dependencies:
        raise SmokeFailure(
            "L1 requires healthy LLM and embedding checks: "
            + ", ".join(failed_strict_dependencies),
            stage="l1-prerequisites",
        )
    resources: dict[str, str] = {}
    warnings: list[str] = []
    primary_failure: SmokeFailure | None = None
    stage = "auth-register"
    headers = {"X-Tenant-Id": config.tenant_id}

    timeout = httpx.Timeout(config.timeout_seconds)
    async with httpx.AsyncClient(
        base_url=config.api_base_url,
        timeout=timeout,
        transport=transport,
    ) as client:
        try:
            started = perf_counter()
            register = await client.post(
                "/auth/register",
                headers=headers,
                json={"email": config.email, "password": config.password},
            )
            _require_status(register, stage, {201, 409})
            checks.append(_check(stage, "PASS", f"HTTP {register.status_code}", started))

            stage = "auth-login"
            started = perf_counter()
            login = await client.post(
                "/auth/login",
                headers=headers,
                json={"email": config.email, "password": config.password},
            )
            _require_status(login, stage)
            headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            checks.append(_check(stage, "PASS", "authenticated", started))

            stage = "space-create"
            started = perf_counter()
            run_id = uuid4().hex[:12]
            space = await client.post(
                "/spaces",
                headers=headers,
                json={"name": f"R0 smoke {run_id}"},
            )
            _require_status(space, stage, {201})
            resources["space_id"] = str(space.json()["space_id"])
            checks.append(_check(stage, "PASS", resources["space_id"], started))

            stage = "document-upload"
            started = perf_counter()
            with fixture_path.open("rb") as handle:
                upload = await client.post(
                    "/documents/upload",
                    params={"space_id": resources["space_id"]},
                    headers=headers,
                    files={"file": (fixture_path.name, handle, "text/markdown")},
                )
            _require_status(upload, stage, {202})
            resources["document_id"] = str(upload.json()["document_id"])
            checks.append(_check(stage, "PASS", resources["document_id"], started))

            stage = "document-workflow"
            started = perf_counter()
            await wait_for_document(client, resources["document_id"], config.timeout_seconds)
            checks.append(_check(stage, "PASS", "COMPLETED", started))

            stage = "search"
            started = perf_counter()
            search = await client.get(
                "/search",
                params={
                    "q": CODEWORD,
                    "space_id": resources["space_id"],
                    "top_k": 5,
                    "score_threshold": 0,
                },
                headers=headers,
            )
            _require_status(search, stage)
            if not search_payload_contains(search.json(), CODEWORD):
                raise SmokeFailure(
                    f"search results do not contain {CODEWORD}",
                    stage=stage,
                    trace_id=_trace_id(search),
                )
            checks.append(_check(stage, "PASS", f"found {CODEWORD}", started))

            stage = "session-create"
            started = perf_counter()
            session = await client.post(
                "/sessions",
                headers=headers,
                json={"title": "R0 smoke", "space_id": resources["space_id"]},
            )
            _require_status(session, stage, {201})
            resources["session_id"] = str(session.json()["session_id"])
            checks.append(_check(stage, "PASS", resources["session_id"], started))

            stage = "chat"
            started = perf_counter()
            async with client.stream(
                "POST",
                "/chat",
                headers=headers,
                json={
                    "query": QUESTION,
                    "session_id": resources["session_id"],
                    "space_id": resources["space_id"],
                    "top_k": 5,
                    "score_threshold": 0,
                },
            ) as chat:
                _require_status(chat, stage)
                lines = [line async for line in chat.aiter_lines()]
                events = parse_sse(lines)
            if CODEWORD not in events.answer:
                raise SmokeFailure(
                    f"chat answer does not contain {CODEWORD}",
                    stage=stage,
                    trace_id=_trace_id(chat),
                )
            if not any(
                citation.get("doc_id") == resources["document_id"] for citation in events.citations
            ):
                raise SmokeFailure(
                    "chat did not cite the uploaded document",
                    stage=stage,
                    trace_id=_trace_id(chat),
                )
            checks.append(_check(stage, "PASS", "answer and citation verified", started))
        except Exception as exc:
            primary_failure = _as_smoke_failure(exc, stage)
        finally:
            if document_id := resources.get("document_id"):
                await _cleanup_resource(
                    client,
                    f"/documents/{document_id}",
                    f"document {document_id}",
                    warnings,
                )
            if space_id := resources.get("space_id"):
                await _cleanup_resource(
                    client,
                    f"/spaces/{space_id}",
                    f"space {space_id}",
                    warnings,
                )

    if primary_failure is not None:
        primary_failure.resources = dict(resources)
        primary_failure.warnings.extend(warnings)
        raise primary_failure

    return {
        "level": "L1",
        "status": "PASS",
        "checks": checks,
        "resources": resources,
        "warnings": warnings,
    }


def config_from_env(args: argparse.Namespace) -> SmokeConfig:
    return SmokeConfig(
        api_base_url=args.api_base_url,
        web_base_url=args.web_base_url,
        tenant_id=os.environ.get("SMOKE_TENANT_ID", "r0-smoke"),
        email=os.environ.get("SMOKE_EMAIL", "r0-smoke@local.invalid"),
        password=os.environ["SMOKE_PASSWORD"],
        timeout_seconds=args.timeout_seconds,
    )


async def run_smoke(config: SmokeConfig, level: str) -> dict[str, object]:
    if level.lower() == "l0":
        return await run_l0(config)
    return await run_l1(config)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the Incipit local runtime")
    parser.add_argument("--level", choices=["l0", "l1"], default="l1")
    parser.add_argument("--api-base-url", default="http://api:8000")
    parser.add_argument("--web-base-url", default="http://web")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    password = os.environ.get("SMOKE_PASSWORD", "")

    def safe_detail(exc: Exception) -> str:
        detail = str(exc) or type(exc).__name__
        return detail.replace(password, "[REDACTED]") if password else detail

    try:
        config = config_from_env(args)
        result = await run_smoke(config, args.level)
    except SmokeFailure as exc:
        failure: dict[str, Any] = {
            "level": args.level.upper(),
            "status": "FAIL",
            "stage": exc.stage or "unknown",
            "detail": safe_detail(exc),
            "resources": exc.resources,
            "warnings": exc.warnings,
        }
        if exc.trace_id:
            failure["trace_id"] = exc.trace_id
        print(json.dumps(failure, ensure_ascii=False))
        return 1
    except (httpx.HTTPError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "level": args.level.upper(),
                    "status": "FAIL",
                    "stage": "configuration" if isinstance(exc, KeyError) else "http",
                    "detail": safe_detail(exc),
                    "resources": {},
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "level": args.level.upper(),
                    "status": "FAIL",
                    "stage": "unexpected",
                    "detail": safe_detail(exc),
                    "resources": {},
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        )
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
