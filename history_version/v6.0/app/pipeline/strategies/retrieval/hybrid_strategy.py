from __future__ import annotations

from app.pipeline.core.context import TenantConfig
from app.pipeline.core.pipeline import Pipeline
from app.pipeline.core.registry import registry
from app.pipeline.strategies.base_strategy import BaseStrategy


@registry.strategy("hybrid")
class HybridRetrievalStrategy(BaseStrategy):
    """
    混合检索策略：向量检索 + BM25 关键词检索 → RRF 融合 → Rerank。

    Pipeline：
        QueryRewrite → VectorSearch → KeywordSearch → RRFFusion → Rerank

    租户可通过 TenantConfig.rerank_enabled 控制是否启用精排。
    """

    strategy_id = "hybrid"
    hooks: list = []    # Phase 3 开启 ObservabilityHook

    def build_pipeline(self, doc_type: str, config: TenantConfig) -> Pipeline:
        return (
            Pipeline.start(registry.get_stage("query_rewrite"))
            .then(registry.get_stage("vector_search"))
            .then(registry.get_stage("keyword_search"))
            .then(registry.get_stage("rrf_fusion"))
            .then(registry.get_stage("rerank"))
        )
