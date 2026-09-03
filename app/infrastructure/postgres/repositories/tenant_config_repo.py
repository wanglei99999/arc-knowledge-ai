from __future__ import annotations

import json

from sqlalchemy import text

from app.infrastructure.postgres.client import get_session


class TenantConfigRepository:
    """tenant_configs 表 CRUD。"""

    async def get(self, tenant_id: str) -> dict | None:
        """查询租户配置，不存在返回 None。"""
        sql = text("SELECT * FROM tenant_configs WHERE tenant_id = :tenant_id")
        async with get_session() as session:
            result = await session.execute(sql, {"tenant_id": tenant_id})
            row = result.fetchone()
            if row is None:
                return None
            return dict(row._mapping)

    async def upsert(
        self,
        tenant_id: str,
        default_llm_provider: str,
        default_llm_model: str,
        allowed_models: list[str],
    ) -> dict:
        """新建或更新租户配置，返回更新后的行。"""
        sql = text("""
            INSERT INTO tenant_configs
                (tenant_id, default_llm_provider, default_llm_model, allowed_models)
            VALUES
                (:tenant_id, :provider, :model, :allowed::jsonb)
            ON CONFLICT (tenant_id) DO UPDATE SET
                default_llm_provider = EXCLUDED.default_llm_provider,
                default_llm_model    = EXCLUDED.default_llm_model,
                allowed_models       = EXCLUDED.allowed_models,
                updated_at           = NOW()
            RETURNING *
        """)
        async with get_session() as session:
            result = await session.execute(
                sql,
                {
                    "tenant_id": tenant_id,
                    "provider": default_llm_provider,
                    "model": default_llm_model,
                    "allowed": json.dumps(allowed_models),
                },
            )
            row = result.fetchone()
            return dict(row._mapping)
