from __future__ import annotations

import asyncio
import logging
import uuid

import httpx

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import HealthStatus, ParsedDocument, ParserProvider

logger = logging.getLogger(__name__)

# MinerU 单任务服务，全局信号量保证同一时刻只有一个请求在处理
_mineru_sem = asyncio.Semaphore(1)


@registry.provider("mineru_parser")
class MinerUParserProvider(ParserProvider):
    """
    对接内部 MinerU 文档解析服务（HTTP 接口）。

    使用 POST /file_parse 同步端点，返回 Markdown 格式文本。
    支持 PDF、图片、DOCX、PPTX、XLSX。

    服务地址通过 MINERU_SERVICE_URL 环境变量配置。
    """

    provider_id = "mineru_parser"
    _SUPPORTED = {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/bmp",
        "image/webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",   # docx
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", # pptx
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",         # xlsx
    }

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

        original_filename = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        # 每次提交加 UUID 前缀，避免 Temporal 重试时 MinerU 返回 409 Conflict
        filename = f"{uuid.uuid4().hex}_{original_filename}"

        form: dict[str, str] = {
            "backend": settings.mineru_backend,
            "parse_method": "auto",
            "return_md": "true",
            "formula_enable": "true",
            "table_enable": "true",
            "lang_list": settings.mineru_lang_list[0] if settings.mineru_lang_list else "ch",
        }
        if settings.mineru_server_url:
            form["server_url"] = settings.mineru_server_url

        async with _mineru_sem:
            max_attempts = 30
            retry_interval = 20
            async with httpx.AsyncClient(timeout=600) as client:
                for attempt in range(max_attempts):
                    resp = await client.post(
                        f"{self._base_url}/file_parse",
                        data=form,
                        files=[("files", (filename, data))],
                    )
                    if resp.status_code == 409:
                        if attempt < max_attempts - 1:
                            logger.warning(
                                "MinerU busy (409), waiting %ds before retry %d/%d",
                                retry_interval, attempt + 1, max_attempts,
                            )
                            await asyncio.sleep(retry_interval)
                            continue
                    if resp.status_code == 422:
                        logger.error("MinerU 422 detail: %s", resp.text)
                    resp.raise_for_status()
                    break

        body = resp.json()

        # 响应格式：{"results": {"<filename>": {"md_content": "..."}}}
        results = body.get("results", {}) if isinstance(body, dict) else {}
        if isinstance(results, dict):
            first = next(iter(results.values()), {})
        elif isinstance(results, list):
            first = results[0] if results else {}
        else:
            first = {}
        md_text = first.get("md_content", "") if isinstance(first, dict) else ""

        if not md_text:
            logger.warning("MinerU returned empty md_content for %s", filename)

        title = ""
        for line in md_text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        return ParsedDocument(
            text=md_text,
            title=title,
            metadata={
                "provider": "mineru",
                "backend": settings.mineru_backend,
                "char_count": len(md_text),
            },
        )
