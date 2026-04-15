from __future__ import annotations

import re

from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase

# tenant_id 允许的格式：字母、数字、连字符，3~64 位
_TENANT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{1,62}[a-zA-Z0-9]$")


class TenantGuard(BaseHook):
    """
    PRE_PIPELINE Hook：校验 tenant_id 合法性。

    Phase 3 范围：格式验证 + 非空检查。
    Phase 4 可升级为：查询租户注册表，验证租户状态（active / suspended）。
    """

    phase = Phase.PRE_PIPELINE
    priority = 10

    async def handle(self, event: HookEvent) -> HookResult:
        tenant_id = event.ctx.tenant_id

        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is empty")

        if not _TENANT_ID_RE.match(tenant_id):
            raise ValueError(
                f"Invalid tenant_id format: {tenant_id!r}. "
                "Must be 3-64 alphanumeric characters or hyphens."
            )

        return HookResult.CONTINUE
