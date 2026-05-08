from __future__ import annotations

import logging
from datetime import date

from app.infrastructure.postgres.repositories.model_config_repo import ModelConfigRepository
from app.infrastructure.postgres.repositories.usage_repo import UsageRepository
from app.infrastructure.telemetry.metrics import LLM_COST_USD_TOTAL, LLM_TOKENS_TOTAL
from app.pipeline.core.events import DomainEvent, EventType, event_bus

logger = logging.getLogger(__name__)

_usage_repo = UsageRepository()
_model_config_repo = ModelConfigRepository()


def register_handlers() -> None:
    """在应用启动时调用，将 TOKEN_CONSUMED 事件处理器注册到 EventBus。"""

    @event_bus.subscribe(EventType.TOKEN_CONSUMED)
    async def handle_token_consumed(event: DomainEvent) -> None:
        tenant_id = event.tenant_id
        model = event.payload.get("model", "unknown")
        input_tokens = int(event.payload.get("input_tokens", 0))
        output_tokens = int(event.payload.get("output_tokens", 0))

        # 查模型单价，计算费用（写入时快照，反映调用时真实单价）
        config = await _model_config_repo.get(model)
        if config:
            cost_usd = (
                input_tokens / 1000 * float(config["input_cost_per_1k"])
                + output_tokens / 1000 * float(config["output_cost_per_1k"])
            )
        else:
            cost_usd = 0.0

        try:
            await _usage_repo.record(
                tenant_id=tenant_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )
        except Exception as e:
            logger.warning("Failed to record usage for tenant %s: %s", tenant_id, e)
            return

        # 更新 Prometheus 指标
        LLM_TOKENS_TOTAL.labels(tenant_id=tenant_id, model=model, type="input").inc(input_tokens)
        LLM_TOKENS_TOTAL.labels(tenant_id=tenant_id, model=model, type="output").inc(output_tokens)
        LLM_COST_USD_TOTAL.labels(tenant_id=tenant_id, model=model).inc(cost_usd)


class UsageService:
    def __init__(self) -> None:
        self._repo = UsageRepository()

    async def query_by_tenant(
        self,
        tenant_id: str,
        start: date,
        end: date,
    ) -> dict:
        data = await self._repo.query_by_tenant(tenant_id, start, end)
        return {
            "tenant_id": tenant_id,
            "period": {"start": str(start), "end": str(end)},
            **data,
        }

    async def get_today_spend(self, tenant_id: str) -> float:
        return await self._repo.get_today_spend(tenant_id)
