from __future__ import annotations

import logging

import httpx

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import HealthStatus, RerankProvider

logger = logging.getLogger(__name__)


@registry.provider("infinity_rerank")
class InfinityRerankProvider(RerankProvider):
    """
    通过 Infinity 推理服务调用 BGE Reranker，模型运行在独立进程中。
    与应用完全隔离，Provider 本身无状态，符合 registry 的设计约定。

    Infinity 服务：https://github.com/michaelfeil/infinity
    启动方式见 docker-compose.yml infinity 服务配置。
    """

    provider_id = "infinity_rerank"

    def __init__(self) -> None:
        self._base_url = settings.infinity_base_url.rstrip("/")
        self._model = settings.infinity_rerank_model

    async def health_check(self) -> HealthStatus:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._base_url}/health")
                return HealthStatus.HEALTHY if r.status_code == 200 else HealthStatus.DEGRADED
        except Exception:
            return HealthStatus.UNHEALTHY

    async def rerank(
        self,
        ctx: ProcessingContext,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        """
        返回 [(原始索引, score)]，按相关性降序，最多 top_n 条。
        Infinity 已按 relevance_score 降序返回，直接映射即可。
        """
        if not documents:
            return []

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base_url}/rerank",
                json={
                    "model":     self._model,
                    "query":     query,
                    "documents": documents,
                    "top_n":     top_n,
                },
            )
            resp.raise_for_status()

        results = resp.json()["results"]
        return [(r["index"], r["relevance_score"]) for r in results]
