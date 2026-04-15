from __future__ import annotations

from app.domain.retrieval import SearchHit
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage


@registry.stage("rerank")
class RerankStage(BaseStage[list[SearchHit], list[SearchHit]]):
    """
    Rerank Stage（当前为 pass-through）。

    Phase 3 接入 RerankProvider（如 Cohere Rerank / BGE-Reranker），
    对 RRF 融合后的候选集做精排。

    当前实现：若租户配置 rerank_enabled=False 或 RerankProvider 未注册，直接透传。
    """

    name = "rerank"

    async def _execute(
        self,
        ctx: ProcessingContext,
        hits: list[SearchHit],
    ) -> list[SearchHit]:
        if not ctx.config.rerank_enabled:
            return hits

        try:
            from app.pipeline.core.registry import registry as _reg
            provider = _reg.get_provider("rerank")  # type: ignore[assignment]
        except Exception:
            # RerankProvider 未注册时透传
            return hits

        if not hits:
            return hits

        # 获取 chunk 文本用于 rerank（需要 ctx.metadata 中的 chunk 内容）
        # VectorSearch / KeywordSearch 阶段尚未拉取文本，pass-through 即可
        # 真实 rerank 需在此之前从 PG 拉取 content
        return hits
