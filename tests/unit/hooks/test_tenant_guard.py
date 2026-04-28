"""TenantGuard 单元测试"""
import pytest

from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.hook import HookEvent, HookResult, Phase
from app.pipeline.hooks.tenant_guard import TenantGuard


def _make_ctx(tenant_id: str) -> ProcessingContext:
    quota = QuotaSnapshot(
        max_documents=100, max_storage_bytes=1024, max_api_calls_per_day=1000,
        used_documents=0, used_storage_bytes=0, used_api_calls_today=0,
    )
    return ProcessingContext.create(
        tenant_id=tenant_id,
        document_id="doc-1",
        quota=quota,
        config=TenantConfig(tenant_id=tenant_id),
    )


def _event(tenant_id: str) -> HookEvent:
    return HookEvent(phase=Phase.PRE_PIPELINE, ctx=_make_ctx(tenant_id))


@pytest.fixture
def guard() -> TenantGuard:
    return TenantGuard()


async def test_valid_tenant_id_continues(guard: TenantGuard) -> None:
    result = await guard.handle(_event("acme-corp"))
    assert result == HookResult.CONTINUE


async def test_valid_tenant_id_with_numbers(guard: TenantGuard) -> None:
    result = await guard.handle(_event("tenant123"))
    assert result == HookResult.CONTINUE


async def test_empty_tenant_id_raises(guard: TenantGuard) -> None:
    with pytest.raises(ValueError, match="empty"):
        await guard.handle(_event(""))


async def test_whitespace_tenant_id_raises(guard: TenantGuard) -> None:
    with pytest.raises(ValueError, match="empty"):
        await guard.handle(_event("   "))


async def test_too_short_tenant_id_raises(guard: TenantGuard) -> None:
    # 少于 3 位
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        await guard.handle(_event("ab"))


async def test_special_chars_raises(guard: TenantGuard) -> None:
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        await guard.handle(_event("tenant@corp"))


async def test_underscore_raises(guard: TenantGuard) -> None:
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        await guard.handle(_event("tenant_corp"))


async def test_leading_hyphen_raises(guard: TenantGuard) -> None:
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        await guard.handle(_event("-tenant"))


async def test_trailing_hyphen_raises(guard: TenantGuard) -> None:
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        await guard.handle(_event("tenant-"))


async def test_max_length_valid(guard: TenantGuard) -> None:
    # 64 位
    tenant_id = "a" + "b" * 62 + "c"
    result = await guard.handle(_event(tenant_id))
    assert result == HookResult.CONTINUE


async def test_over_max_length_raises(guard: TenantGuard) -> None:
    # 65 位
    tenant_id = "a" * 65
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        await guard.handle(_event(tenant_id))
