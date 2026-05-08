from __future__ import annotations

from app.infrastructure.postgres.repositories.model_config_repo import ModelConfigRepository


class ModelConfigService:
    def __init__(self) -> None:
        self._repo = ModelConfigRepository()

    async def list_all(self) -> list[dict]:
        return await self._repo.list_all()

    async def upsert(
        self,
        model_id: str,
        input_cost_per_1k: float,
        output_cost_per_1k: float,
        context_window: int,
    ) -> dict:
        return await self._repo.upsert(
            model_id=model_id,
            input_cost_per_1k=input_cost_per_1k,
            output_cost_per_1k=output_cost_per_1k,
            context_window=context_window,
        )

    async def delete(self, model_id: str) -> bool:
        return await self._repo.delete(model_id)

    async def get_cost(self, model_id: str) -> tuple[float, float]:
        """返回 (input_cost_per_1k, output_cost_per_1k)，未配置时返回 (0, 0)。"""
        config = await self._repo.get(model_id)
        if not config:
            return 0.0, 0.0
        return float(config["input_cost_per_1k"]), float(config["output_cost_per_1k"])
