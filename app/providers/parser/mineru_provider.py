from __future__ import annotations

import logging

import httpx

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import HealthStatus, ParsedDocument, ParserProvider

logger = logging.getLogger(__name__)


@registry.provider("mineru_parser")
class MinerUParserProvider(ParserProvider):
    """
    通过 MinerU 容器服务解析文档，同时支持原生 PDF 和扫描件。
    MinerU 自动判断解析模式（txt / ocr），输出 Markdown 格式文本。
    Provider 本身无状态，符合 registry 的设计约定。

    服务启动方式见 docker-compose.yml mineru 服务配置。
    """

    provider_id = "mineru_parser"
    _SUPPORTED = {"application/pdf", "image/jpeg", "image/png", "image/tiff", "image/bmp"}

    def __init__(self) -> None:
        self._base_url = settings.mineru_service_url.rstrip("/")

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

        # MinerU 处理复杂 PDF（含大量图表/公式）耗时较长，超时设为 300s
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self._base_url}/parse",
                files={"file": (filename, data)},
            )
            resp.raise_for_status()

        body = resp.json()

        return ParsedDocument(
            text=body["text"],
            title=body.get("title", ""),
            metadata={
                "provider": "mineru",
                "is_ocr": body.get("is_ocr", False),
                "char_count": body.get("char_count", len(body["text"])),
            },
        )
