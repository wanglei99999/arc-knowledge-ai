from __future__ import annotations

import logging

import httpx

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import HealthStatus, ParsedDocument, ParserProvider

logger = logging.getLogger(__name__)


@registry.provider("paddleocr_parser")
class PaddleOCRParserProvider(ParserProvider):
    """
    通过 OCR 容器服务解析扫描件，模型运行在独立容器中。
    Provider 本身无状态，符合 registry 的设计约定。

    OCR 服务启动方式见 docker-compose.yml paddleocr 服务配置。
    """

    provider_id = "paddleocr_parser"
    _SUPPORTED = {"image/jpeg", "image/png", "image/tiff", "image/bmp", "application/pdf"}

    def __init__(self) -> None:
        self._base_url = settings.ocr_service_url.rstrip("/")

    def supports(self, mime_type: str) -> bool:
        return mime_type in self._SUPPORTED

    async def health_check(self) -> HealthStatus:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._base_url}/health")
                return HealthStatus.HEALTHY if r.status_code == 200 else HealthStatus.DEGRADED
        except Exception:
            return HealthStatus.UNHEALTHY

    async def parse(self, ctx: ProcessingContext, file_path: str) -> ParsedDocument:
        with open(file_path, "rb") as f:
            data = f.read()

        filename = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self._base_url}/ocr",
                files={"file": (filename, data)},
            )
            resp.raise_for_status()

        body = resp.json()
        lines = body["text"].splitlines()

        return ParsedDocument(
            text=body["text"],
            title=lines[0] if lines else "",
            metadata={"provider": "paddleocr", "line_count": body["line_count"]},
        )
