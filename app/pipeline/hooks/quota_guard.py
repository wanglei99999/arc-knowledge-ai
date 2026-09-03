from __future__ import annotations

import logging

from app.infrastructure.redis.client import get_redis
from app.pipeline.core.exceptions import QuotaExceededError
from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase

logger = logging.getLogger(__name__)

_SPEND_CACHE_TTL = 60  # 今日费用缓存 1 分钟，平衡精度与 DB 压力


class QuotaGuard(BaseHook):
    """
    PRE_PIPELINE Hook：检查租户 API 调用、存储和日费用配额。

    配额快照在请求开始时固化在 ProcessingContext.quota，
    保证同一请求内多个 Stage 看到一致的配额状态。
    """

    phase = Phase.PRE_PIPELINE
    priority = 20

    async def handle(self, event: HookEvent) -> HookResult:
        quota = event.ctx.quota
        tenant_id = event.ctx.tenant_id

        if not quota.has_api_quota():
            logger.warning(
                "Quota exceeded for tenant %s: api_calls %d/%d",
                tenant_id,
                quota.used_api_calls_today,
                quota.max_api_calls_per_day,
            )
            raise QuotaExceededError(
                f"Daily API call quota exceeded for tenant {tenant_id!r}. "
                f"Used: {quota.used_api_calls_today}, "
                f"Limit: {quota.max_api_calls_per_day}"
            )

        if not quota.has_storage_quota():
            logger.warning(
                "Storage quota exceeded for tenant %s: %d/%d bytes",
                tenant_id,
                quota.used_storage_bytes,
                quota.max_storage_bytes,
            )
            raise QuotaExceededError(
                f"Storage quota exceeded for tenant {tenant_id!r}. "
                f"Used: {quota.used_storage_bytes} bytes, "
                f"Limit: {quota.max_storage_bytes} bytes"
            )

        if not quota.has_spend_quota():
            logger.warning(
                "Spend quota exceeded for tenant %s: $%.4f/$%.4f",
                tenant_id,
                quota.used_spend_today,
                quota.max_spend_per_day,
            )
            raise QuotaExceededError(
                f"Daily spend quota exceeded for tenant {tenant_id!r}. "
                f"Used: ${quota.used_spend_today:.4f}, "
                f"Limit: ${quota.max_spend_per_day:.4f}"
            )

        return HookResult.CONTINUE


async def get_today_spend_cached(tenant_id: str) -> float:
    """从 Redis 缓存读取今日费用，缓存未命中时查 DB 并回填。"""
    cache_key = f"quota:spend:{tenant_id}"
    redis = get_redis()

    try:
        cached = await redis.get(cache_key)
        if cached is not None:
            return float(cached)
    except Exception:
        pass

    # 缓存未命中：查 DB
    from app.infrastructure.postgres.repositories.usage_repo import UsageRepository

    spend = await UsageRepository().get_today_spend(tenant_id)

    try:
        await redis.set(cache_key, str(spend), ex=_SPEND_CACHE_TTL)
    except Exception:
        pass

    return spend
