from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import text

from app.infrastructure.postgres.client import get_session

logger = logging.getLogger(__name__)


class UsageRepository:
    async def record(
        self,
        tenant_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        async with get_session() as session:
            await session.execute(
                text("""
                    INSERT INTO usage_records
                        (tenant_id, model, input_tokens, output_tokens, cost_usd)
                    VALUES
                        (:tenant_id, :model, :input_tokens, :output_tokens, :cost_usd)
                """),
                {
                    "tenant_id": tenant_id,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                },
            )

    async def query_by_tenant(
        self,
        tenant_id: str,
        start: date,
        end: date,
    ) -> dict:
        start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)

        async with get_session() as session:
            # 汇总
            total_row = await session.execute(
                text("""
                    SELECT
                        COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                        COALESCE(SUM(output_tokens), 0) AS output_tokens,
                        COALESCE(SUM(cost_usd), 0)      AS cost_usd
                    FROM usage_records
                    WHERE tenant_id = :tenant_id
                      AND created_at BETWEEN :start AND :end
                """),
                {"tenant_id": tenant_id, "start": start_dt, "end": end_dt},
            )
            total = total_row.mappings().one()

            # 按模型分组
            model_rows = await session.execute(
                text("""
                    SELECT
                        model,
                        SUM(input_tokens)  AS input_tokens,
                        SUM(output_tokens) AS output_tokens,
                        SUM(cost_usd)      AS cost_usd
                    FROM usage_records
                    WHERE tenant_id = :tenant_id
                      AND created_at BETWEEN :start AND :end
                    GROUP BY model
                    ORDER BY cost_usd DESC
                """),
                {"tenant_id": tenant_id, "start": start_dt, "end": end_dt},
            )
            by_model = [dict(r) for r in model_rows.mappings()]

        return {
            "total_input_tokens":  int(total["input_tokens"]),
            "total_output_tokens": int(total["output_tokens"]),
            "total_cost_usd":      float(total["cost_usd"]),
            "by_model":            by_model,
        }

    async def get_today_spend(self, tenant_id: str) -> float:
        today = datetime.now(timezone.utc).date()
        start_dt = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

        async with get_session() as session:
            row = await session.execute(
                text("""
                    SELECT COALESCE(SUM(cost_usd), 0) AS total
                    FROM usage_records
                    WHERE tenant_id = :tenant_id
                      AND created_at >= :start
                """),
                {"tenant_id": tenant_id, "start": start_dt},
            )
            return float(row.scalar_one())
