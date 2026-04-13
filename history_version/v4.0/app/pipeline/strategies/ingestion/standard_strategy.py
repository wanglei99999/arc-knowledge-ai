from __future__ import annotations

from app.pipeline.core.context import TenantConfig
from app.pipeline.core.pipeline import Pipeline
from app.pipeline.core.registry import registry
from app.pipeline.stages.chunking.token_chunker import TokenChunkerStage
from app.pipeline.stages.embedding.embed_stage import EmbedStage
from app.pipeline.hooks.idempotency_guard import IdempotencyGuard
from app.pipeline.hooks.observability_hook import ObservabilityHook
from app.pipeline.hooks.quota_guard import QuotaGuard
from app.pipeline.hooks.tenant_guard import TenantGuard
from app.pipeline.stages.embedding.es_index_stage import ESIndexStage
from app.pipeline.stages.embedding.milvus_index_stage import MilvusIndexStage
from app.pipeline.stages.parsing.parser_stage import ParserStage
from app.pipeline.strategies.base_strategy import BaseStrategy


@registry.strategy("standard")
class StandardIngestionStrategy(BaseStrategy):
    """
    标准文档入库策略。

    Pipeline：ParserStage → TokenChunkerStage → EmbedStage → MilvusIndexStage → ESIndexStage
    Hooks（按 priority 顺序）：TenantGuard(10) → QuotaGuard(20) → IdempotencyGuard(30) → ObservabilityHook(100)
    """

    strategy_id = "standard"
    hooks = [TenantGuard, QuotaGuard, IdempotencyGuard, ObservabilityHook]

    def build_pipeline(self, doc_type: str, config: TenantConfig) -> Pipeline:
        return (
            Pipeline.start(ParserStage())
            .then(TokenChunkerStage())
            .then(EmbedStage())
            .then(MilvusIndexStage())
            .then(ESIndexStage())
        )
