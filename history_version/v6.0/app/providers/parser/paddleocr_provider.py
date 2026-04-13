from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import ParsedDocument, ParserProvider


@registry.provider("paddleocr_parser")
class PaddleOCRParserProvider(ParserProvider):
    """
    扫描件 OCR 解析 Provider，基于 PaddleOCR。

    适用于：扫描版 PDF、图片（JPG / PNG / TIFF）
    不适用于：原生 PDF、Word、Excel（用 unstructured_parser）

    PaddleOCR 安装需要系统依赖，pyproject.toml 中已注释，
    使用前执行：pip install paddlepaddle paddleocr
    """

    _SUPPORTED = {"image/jpeg", "image/png", "image/tiff", "image/bmp"}

    def supports(self, mime_type: str) -> bool:
        return mime_type in self._SUPPORTED or mime_type == "application/pdf"

    async def parse(self, ctx: ProcessingContext, file_path: str) -> ParsedDocument:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        # 懒加载，避免未安装时 import 报错
        from paddleocr import PaddleOCR  # type: ignore[import]

        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        result = ocr.ocr(file_path, cls=True)

        lines: list[str] = []
        for page in result:
            if page is None:
                continue
            for line in page:
                # line = [bbox, (text, confidence)]
                text, confidence = line[1]
                if confidence >= 0.7:
                    lines.append(text)

        full_text = "\n".join(lines)
        title = lines[0] if lines else ""

        return ParsedDocument(
            text=full_text,
            title=title,
            metadata={"provider": "paddleocr", "line_count": len(lines)},
        )
