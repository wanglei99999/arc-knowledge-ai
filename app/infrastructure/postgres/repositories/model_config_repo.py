from __future__ import annotations

import logging

from sqlalchemy import text

from app.infrastructure.postgres.client import get_session

logger = logging.getLogger(__name__)


class ModelConfigRepository:
    async def get(self, model_id: str) -> dict | None:
        async with get_session() as session:
            row = await session.execute(
                text("""
                    SELECT model_id, input_cost_per_1k, output_cost_per_1k,
                           context_window, updated_at
                    FROM model_configs
                    WHERE model_id = :model_id
                """),
                {"model_id": model_id},
            )
            result = row.mappings().first()
            return dict(result) if result else None

    async def list_all(self) -> list[dict]:
        async with get_session() as session:
            rows = await session.execute(
                text("""
                    SELECT model_id, input_cost_per_1k, output_cost_per_1k,
                           context_window, updated_at
                    FROM model_configs
                    ORDER BY model_id
                """)
            )
            return [dict(r) for r in rows.mappings()]

    async def upsert(
        self,
        model_id: str,
        input_cost_per_1k: float,
        output_cost_per_1k: float,
        context_window: int,
    ) -> dict:
        async with get_session() as session:
            row = await session.execute(
                text("""
                    INSERT INTO model_configs
                        (model_id, input_cost_per_1k, output_cost_per_1k, context_window)
                    VALUES
                        (:model_id, :input_cost_per_1k, :output_cost_per_1k, :context_window)
                    ON CONFLICT (model_id) DO UPDATE SET
                        input_cost_per_1k  = EXCLUDED.input_cost_per_1k,
                        output_cost_per_1k = EXCLUDED.output_cost_per_1k,
                        context_window     = EXCLUDED.context_window,
                        updated_at         = NOW()
                    RETURNING model_id, input_cost_per_1k, output_cost_per_1k,
                              context_window, updated_at
                """),
                {
                    "model_id": model_id,
                    "input_cost_per_1k": input_cost_per_1k,
                    "output_cost_per_1k": output_cost_per_1k,
                    "context_window": context_window,
                },
            )
            return dict(row.mappings().one())

    async def delete(self, model_id: str) -> bool:
        async with get_session() as session:
            result = await session.execute(
                text("DELETE FROM model_configs WHERE model_id = :model_id"),
                {"model_id": model_id},
            )
            return result.rowcount > 0
