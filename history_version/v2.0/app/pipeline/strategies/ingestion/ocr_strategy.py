from __future__ import annotations

from app.pipeline.core.context import TenantConfig
from app.pipeline.core.pipeline import Pipeline
from app.pipeline.core.registry import registry
from app.pipeline.stages.chunking.token_chunker import TokenChunkerStage
from app.pipeline.stages.embedding.embed_stage import EmbedStage
from app.pipeline.stages.embedding.milvus_index_stage import MilvusIndexStage
from app.pipeline.stages.parsing.parser_stage import ParserStage
from app.pipeline.strategies.base_strategy import BaseStrategy


@registry.strategy("ocr")
class OCRIngestionStrategy(BaseStrategy):
    """
    扫描件入库策略，使用 PaddleOCR 解析。

    Pipeline：ParserStage(paddleocr) → TokenChunkerStage → EmbedStage → MilvusIndexStage

    与 StandardIngestionStrategy 的区别：
    - parser_provider 强制使用 paddleocr_parser
    - chunk_size 默认更小（扫描件 OCR 结果噪声多，短段更准确）
    """

    strategy_id = "ocr"
    hooks = []  # Phase 3 再填充

    def build_pipeline(self, doc_type: str, config: TenantConfig) -> Pipeline:
        # 强制走 OCR provider，覆盖租户配置中的 parser_provider
        parser = ParserStage(provider_id="paddleocr_parser")
        return (
            Pipeline.start(parser)
            .then(TokenChunkerStage())
            .then(EmbedStage())
            .then(MilvusIndexStage())
        )
