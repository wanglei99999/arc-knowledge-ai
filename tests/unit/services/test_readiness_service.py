import asyncio

import pytest

from app.services.readiness_service import ReadinessService


async def succeeds() -> None:
    return None


async def fails() -> None:
    raise ConnectionError("offline")


@pytest.mark.asyncio
async def test_required_failure_is_unhealthy():
    report = await ReadinessService(
        required={"postgres": fails}, optional={"llm": succeeds}
    ).check()

    assert report.status == "unhealthy"
    postgres = next(item for item in report.checks if item.name == "postgres")
    assert postgres.required is True
    assert postgres.status == "failed"
    assert postgres.detail == "offline"


@pytest.mark.asyncio
async def test_optional_failure_is_degraded():
    report = await ReadinessService(
        required={"postgres": succeeds}, optional={"llm": fails}
    ).check()

    assert report.status == "degraded"
    llm = next(item for item in report.checks if item.name == "llm")
    assert llm.required is False
    assert llm.status == "failed"


@pytest.mark.asyncio
async def test_all_success_is_healthy():
    report = await ReadinessService(
        required={"postgres": succeeds}, optional={"llm": succeeds}
    ).check()

    assert report.status == "healthy"
    assert all(item.status == "ok" for item in report.checks)


@pytest.mark.asyncio
async def test_checks_run_concurrently():
    started = 0
    both_started = asyncio.Event()

    async def waits_for_peer() -> None:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.1)

    report = await ReadinessService(
        required={"postgres": waits_for_peer, "redis": waits_for_peer},
        optional={},
    ).check()

    assert report.status == "healthy"


@pytest.mark.asyncio
async def test_timeout_is_reported_as_failure():
    async def hangs() -> None:
        await asyncio.sleep(1)

    report = await ReadinessService(
        required={"postgres": hangs},
        optional={},
        timeout_seconds=0.001,
    ).check()

    assert report.status == "unhealthy"
    assert report.checks[0].status == "failed"
    assert "timed out" in report.checks[0].detail
