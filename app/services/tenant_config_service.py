from __future__ import annotations

from app.infrastructure.postgres.repositories.tenant_config_repo import TenantConfigRepository
from app.pipeline.core.context import TenantConfig


class TenantConfigService:
    """
    租户 LLM 配置管理。

    从 DB 加载配置并合并进 TenantConfig；
    不存在时返回全默认值的 TenantConfig，保持兼容。
    """

    def __init__(self) -> None:
        self._repo = TenantConfigRepository()

    async def get_or_default(self, tenant_id: str) -> TenantConfig:
        """
        加载租户配置。DB 有记录则覆盖 llm_provider / default_llm_model / allowed_models；
        无记录则返回带全默认值的 TenantConfig。
        """
        row = await self._repo.get(tenant_id)
        if row is None:
            return TenantConfig(tenant_id=tenant_id)

        allowed: list[str] = row.get("allowed_models") or []
        if isinstance(allowed, str):
            import json
            allowed = json.loads(allowed)

        return TenantConfig(
            tenant_id=tenant_id,
            llm_provider=row["default_llm_provider"],
            default_llm_model=row["default_llm_model"] or "",
            allowed_models=allowed,
        )

    async def update(
        self,
        tenant_id: str,
        default_llm_provider: str,
        default_llm_model: str,
        allowed_models: list[str],
    ) -> dict:
        """新建或更新租户 LLM 配置，返回持久化后的行。"""
        return await self._repo.upsert(
            tenant_id=tenant_id,
            default_llm_provider=default_llm_provider,
            default_llm_model=default_llm_model,
            allowed_models=allowed_models,
        )
