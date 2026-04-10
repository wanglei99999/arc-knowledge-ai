from __future__ import annotations

from app.pipeline.core.context import TenantConfig
from app.pipeline.core.pipeline import Pipeline
from app.pipeline.core.registry import registry
from app.pipeline.stages.chunking.token_chunker import TokenChunkerStage
from app.pipeline.stages.embedding.embed_stage import EmbedStage
from app.pipeline.stages.embedding.milvus_index_stage import MilvusIndexStage
from app.pipeline.stages.parsing.parser_stage import ParserStage
from app.pipeline.strategies.base_strategy import BaseStrategy


@registry.strategy("standard")
class StandardIngestionStrategy(BaseStrategy):
    """
    标准文档入库策略。

    Pipeline：ParserStage → TokenChunkerStage → EmbedStage → MilvusIndexStage

    Phase 0：无 Hook（hooks = []）
    Phase 3 开启后添加：[TenantGuard, ObservabilityHook, QuotaGuard, IdempotencyGuard]
    """

    strategy_id = "standard"
    hooks = []  # Phase 3 再填充

    def build_pipeline(self, doc_type: str, config: TenantConfig) -> Pipeline:
        return (
            Pipeline.start(ParserStage())
            .then(TokenChunkerStage())
            .then(EmbedStage())
            .then(MilvusIndexStage())
        )
