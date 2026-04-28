"""ResilientLLMProvider 单元测试"""
from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.providers.base import ChatMessage, HealthStatus, LLMProvider
from app.providers.llm.resilient_llm import ResilientLLMProvider


# ── Fake Providers ────────────────────────────────────────────────────────────

class FakeLLMProvider(LLMProvider):
    provider_id = "fake_llm"

    def __init__(self, response: str = "ok", fail_times: int = 0) -> None:
        self._response = response
        self._fail_times = fail_times
        self._call_count = 0

    async def health_check(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def generate(self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs) -> str:
        self._call_count += 1
        if self._call_count <= self._fail_times:
            raise RuntimeError(f"Simulated failure #{self._call_count}")
        return self._response

    async def stream_generate(
        self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs
    ) -> AsyncIterator[str]:
        if self._fail_times > 0:
            raise RuntimeError("Simulated stream failure")
        for token in self._response.split():
            yield token


class AlwaysFailLLMProvider(LLMProvider):
    provider_id = "always_fail"

    async def health_check(self) -> HealthStatus:
        return HealthStatus.UNHEALTHY

    async def generate(self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs) -> str:
        raise RuntimeError("Always fails")

    async def stream_generate(
        self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs
    ) -> AsyncIterator[str]:
        raise RuntimeError("Always fails")
        yield  # make it a generator


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ctx() -> ProcessingContext:
    quota = QuotaSnapshot(
        max_documents=100, max_storage_bytes=1024, max_api_calls_per_day=1000,
        used_documents=0, used_storage_bytes=0, used_api_calls_today=0,
    )
    return ProcessingContext.create(
        tenant_id="test-tenant",
        document_id="doc-1",
        quota=quota,
        config=TenantConfig(tenant_id="test-tenant"),
    )


@pytest.fixture
def messages() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="hello")]


# ── generate() 测试 ───────────────────────────────────────────────────────────

async def test_generate_returns_primary_response(ctx, messages) -> None:
    primary = FakeLLMProvider(response="primary answer")
    fallback = FakeLLMProvider(response="fallback answer")

    with patch("app.providers.llm.resilient_llm.registry") as mock_registry:
        mock_registry.get_provider.return_value = fallback
        provider = ResilientLLMProvider(primary, "fake_fallback")
        result = await provider.generate(ctx, messages)

    assert result == "primary answer"
    assert primary._call_count == 1


async def test_generate_falls_back_after_all_retries_fail(ctx, messages) -> None:
    primary = AlwaysFailLLMProvider()
    fallback = FakeLLMProvider(response="fallback answer")

    with patch("app.providers.llm.resilient_llm.registry") as mock_registry, \
         patch("app.providers.llm.resilient_llm.settings") as mock_settings:
        mock_settings.llm_max_retries = 2
        mock_settings.llm_retry_min_wait = 0
        mock_settings.llm_retry_max_wait = 0
        mock_registry.get_provider.return_value = fallback

        provider = ResilientLLMProvider(primary, "fake_fallback")
        result = await provider.generate(ctx, messages)

    assert result == "fallback answer"
    mock_registry.get_provider.assert_called_once_with("fake_fallback")


async def test_generate_succeeds_after_transient_failure(ctx, messages) -> None:
    # 失败 1 次后成功，重试次数 = 3，应该成功
    primary = FakeLLMProvider(response="ok", fail_times=1)

    with patch("app.providers.llm.resilient_llm.settings") as mock_settings:
        mock_settings.llm_max_retries = 3
        mock_settings.llm_retry_min_wait = 0
        mock_settings.llm_retry_max_wait = 0

        provider = ResilientLLMProvider(primary, "fake_fallback")
        result = await provider.generate(ctx, messages)

    assert result == "ok"
    assert primary._call_count == 2  # 1 失败 + 1 成功


# ── stream_generate() 测试 ────────────────────────────────────────────────────

async def test_stream_generate_yields_tokens(ctx, messages) -> None:
    primary = FakeLLMProvider(response="hello world")
    provider = ResilientLLMProvider(primary, "fake_fallback")

    tokens = [t async for t in provider.stream_generate(ctx, messages)]
    assert tokens == ["hello", "world"]


async def test_stream_falls_back_before_first_token(ctx, messages) -> None:
    primary = AlwaysFailLLMProvider()
    fallback = FakeLLMProvider(response="fallback result")

    with patch("app.providers.llm.resilient_llm.registry") as mock_registry:
        mock_registry.get_provider.return_value = fallback
        provider = ResilientLLMProvider(primary, "fake_fallback")
        tokens = [t async for t in provider.stream_generate(ctx, messages)]

    assert tokens == ["fallback", "result"]


async def test_stream_raises_after_partial_tokens(ctx, messages) -> None:
    """已推送部分 token 后出错，不应切换 fallback，直接抛出。"""
    async def _partial_stream(ctx, messages, **kwargs):
        yield "first"
        raise RuntimeError("mid-stream failure")

    primary = FakeLLMProvider()
    primary.stream_generate = _partial_stream

    provider = ResilientLLMProvider(primary, "fake_fallback")

    with pytest.raises(RuntimeError, match="mid-stream failure"):
        async for _ in provider.stream_generate(ctx, messages):
            pass


async def test_health_check_delegates_to_primary(ctx) -> None:
    primary = FakeLLMProvider()
    provider = ResilientLLMProvider(primary, "fake_fallback")
    status = await provider.health_check()
    assert status == HealthStatus.HEALTHY
