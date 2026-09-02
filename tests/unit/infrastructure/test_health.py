from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.health import (
    build_readiness_service,
    check_http_endpoint,
    check_postgres,
    check_redis,
    enabled_optional_services,
)


class ResponseStub:
    def __init__(self, status_code: int):
        self.status_code = status_code


class HttpClientStub:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.request: tuple[str, dict[str, str]] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers):
        self.request = (url, headers)
        return ResponseStub(self.status_code)


@pytest.mark.asyncio
async def test_http_probe_accepts_401_as_reachable(monkeypatch):
    client = HttpClientStub(401)
    monkeypatch.setattr("app.infrastructure.health.httpx.AsyncClient", lambda **_: client)

    await check_http_endpoint("http://model.test/v1/", "secret")

    assert client.request == (
        "http://model.test/v1",
        {"Authorization": "Bearer secret"},
    )


@pytest.mark.asyncio
async def test_http_probe_rejects_500(monkeypatch):
    monkeypatch.setattr(
        "app.infrastructure.health.httpx.AsyncClient",
        lambda **_: HttpClientStub(500),
    )

    with pytest.raises(ConnectionError, match="HTTP 500"):
        await check_http_endpoint("http://model.test/v1")


@pytest.mark.asyncio
async def test_postgres_probe_executes_select_one(monkeypatch):
    session = type("SessionStub", (), {"execute": AsyncMock()})()

    @asynccontextmanager
    async def session_scope():
        yield session

    monkeypatch.setattr("app.infrastructure.health.get_session", session_scope)

    await check_postgres()

    assert str(session.execute.await_args.args[0]) == "SELECT 1"


@pytest.mark.asyncio
async def test_redis_probe_requires_ping_true(monkeypatch):
    redis = type("RedisStub", (), {"ping": AsyncMock(return_value=False)})()
    monkeypatch.setattr("app.infrastructure.health.get_redis", lambda: redis)

    with pytest.raises(ConnectionError, match="PING"):
        await check_redis()


def test_optional_service_parser_normalizes_case_and_whitespace(monkeypatch):
    monkeypatch.setattr(
        "app.infrastructure.health.settings.runtime_optional_services",
        " RERANK, ocr, Observe, rerank ,,",
    )

    assert enabled_optional_services() == {"rerank", "ocr", "observe"}


@pytest.mark.asyncio
async def test_readiness_factory_maps_required_and_profile_checks(monkeypatch):
    import app.infrastructure.health as health

    requested_urls: list[str] = []

    async def succeeds(*args, **kwargs) -> None:
        if args and isinstance(args[0], str):
            requested_urls.append(args[0])

    for name in (
        "check_postgres",
        "check_redis",
        "check_minio",
        "check_milvus",
        "check_elasticsearch",
        "check_temporal",
    ):
        monkeypatch.setattr(health, name, succeeds)
    monkeypatch.setattr(health, "check_http_endpoint", succeeds)
    monkeypatch.setattr(health.settings, "runtime_optional_services", "rerank,ocr,observe")
    monkeypatch.setattr(health.settings, "otlp_endpoint", "http://phoenix:4317")

    report = await build_readiness_service().check()
    checks = {item.name: item.required for item in report.checks}

    assert {name for name, required in checks.items() if required} == {
        "postgres",
        "redis",
        "minio",
        "milvus",
        "elasticsearch",
        "temporal",
    }
    assert {name for name, required in checks.items() if not required} == {
        "llm",
        "embedding",
        "infinity",
        "paddleocr",
        "mineru",
        "phoenix",
    }
    assert "http://phoenix:6006" in requested_urls
