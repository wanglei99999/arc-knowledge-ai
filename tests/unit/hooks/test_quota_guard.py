"""QuotaGuard 单元测试"""
import pytest

from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.exceptions import QuotaExceededError
from app.pipeline.core.hook import HookEvent, HookResult, Phase
from app.pipeline.hooks.quota_guard import QuotaGuard


def _make_ctx(quota: QuotaSnapshot) -> ProcessingContext:
    return ProcessingContext.create(
        tenant_id="test-tenant",
        document_id="doc-1",
        quota=quota,
        config=TenantConfig(tenant_id="test-tenant"),
    )


def _event(quota: QuotaSnapshot) -> HookEvent:
    return HookEvent(phase=Phase.PRE_PIPELINE, ctx=_make_ctx(quota))


def _quota(
    *,
    max_api: int = 1000,
    used_api: int = 0,
    max_storage: int = 1024 * 1024,
    used_storage: int = 0,
) -> QuotaSnapshot:
    return QuotaSnapshot(
        max_documents=100,
        max_storage_bytes=max_storage,
        max_api_calls_per_day=max_api,
        used_documents=0,
        used_storage_bytes=used_storage,
        used_api_calls_today=used_api,
    )


@pytest.fixture
def guard() -> QuotaGuard:
    return QuotaGuard()


async def test_within_quota_continues(guard: QuotaGuard) -> None:
    result = await guard.handle(_event(_quota(used_api=500)))
    assert result == HookResult.CONTINUE


async def test_api_quota_exactly_at_limit_continues(guard: QuotaGuard) -> None:
    # used == max - 1，刚好还有 1 次可用
    result = await guard.handle(_event(_quota(max_api=10, used_api=9)))
    assert result == HookResult.CONTINUE


async def test_api_quota_exceeded_raises(guard: QuotaGuard) -> None:
    quota = _quota(max_api=10, used_api=10)
    with pytest.raises(QuotaExceededError, match="API call quota"):
        await guard.handle(_event(quota))


async def test_api_quota_over_limit_raises(guard: QuotaGuard) -> None:
    quota = _quota(max_api=10, used_api=11)
    with pytest.raises(QuotaExceededError, match="API call quota"):
        await guard.handle(_event(quota))


async def test_storage_quota_exceeded_raises(guard: QuotaGuard) -> None:
    quota = _quota(max_storage=1000, used_storage=1000)
    with pytest.raises(QuotaExceededError, match="Storage quota"):
        await guard.handle(_event(quota))


async def test_storage_within_quota_continues(guard: QuotaGuard) -> None:
    quota = _quota(max_storage=1000, used_storage=999)
    result = await guard.handle(_event(quota))
    assert result == HookResult.CONTINUE


async def test_api_quota_checked_before_storage(guard: QuotaGuard) -> None:
    # 两个都超了，API 先检查，应抛 API quota 错误
    quota = _quota(max_api=5, used_api=10, max_storage=100, used_storage=200)
    with pytest.raises(QuotaExceededError, match="API call quota"):
        await guard.handle(_event(quota))
