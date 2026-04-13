from __future__ import annotations

import logging

from app.pipeline.core.exceptions import QuotaExceededError
from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase

logger = logging.getLogger(__name__)


class QuotaGuard(BaseHook):
    """
    PRE_PIPELINE Hook：检查租户 API 调用和存储配额。

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

        return HookResult.CONTINUE
