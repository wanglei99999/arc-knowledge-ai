from __future__ import annotations

import logging

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import HealthStatus, ParsedDocument, ParserProvider

logger = logging.getLogger(__name__)


@registry.provider("smart_parser")
class SmartParserProvider(ParserProvider):
    """
    两阶段解析 Provider，与 ResilientLLMProvider 模式一致。

    Phase 1: unstructured_parser（快，保留文档结构）
    Phase 2: paddleocr_parser（慢，图像 OCR，仅 PDF 触发）

    触发条件：解析结果文本 < SPARSE_THRESHOLD 且文件为 PDF
    """

    provider_id = "smart_parser"
    SPARSE_THRESHOLD = 100  # 字符数，低于此视为扫描件
    async def parse(self, ctx: ProcessingContext, file_path: str) -> ParsedDocument:
        # Phase 1: 原生文字提取
        primary = registry.get_provider("unstructured_parser")
        result = await primary.parse(ctx, file_path)

        # 检查文本密度：太少且是 PDF → 触发 OCR 降级
        is_pdf = file_path.lower().endswith(".pdf")
        if is_pdf and len(result.text.strip()) < self.SPARSE_THRESHOLD:
            try:
                ocr = registry.get_provider("paddleocr_parser")
                result = await ocr.parse(ctx, file_path)
                # 标记 OCR 已使用，供后续审计
                meta = dict(result.metadata or {})
                meta["ocr_fallback"] = True
                result = ParsedDocument(
                    text=result.text,
                    title=result.title,
                    metadata=meta,
                    page_count=result.page_count,
                )
            except Exception as e:
                logger.warning("SmartParser: OCR fallback failed, keeping original result: %s", e)
        return result
    
    def supports(self, mime_type: str) -> bool:
        return True  # 委托给内部 provider 判断
    
    async def health_check(self) -> HealthStatus:
        primary = registry.get_provider("unstructured_parser")
        return await primary.health_check()
