from __future__ import annotations

import asyncio

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import HealthStatus, ParsedDocument, ParserProvider

_SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/html",
    "text/markdown",
}


@registry.provider("unstructured_parser")
class UnstructuredParserProvider(ParserProvider):
    """
    基于 Unstructured 库的通用文档解析器。

    保留文档结构（标题层级、段落、表格），是 RAG 切片质量的关键。
    IO 密集型，使用 run_in_executor 避免阻塞事件循环。
    """

    provider_id = "unstructured_parser"

    async def parse(
        self,
        ctx: ProcessingContext,
        file_path: str,
    ) -> ParsedDocument:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        from unstructured.partition.auto import partition  # 懒加载，避免启动时开销

        elements = partition(filename=file_path)

        # 提取标题
        title: str | None = None
        for el in elements:
            if el.category == "Title" and str(el).strip():
                title = str(el).strip()
                break

        # 将所有元素拼接为结构化文本（标题换两行，其余换一行）
        parts: list[str] = []
        for el in elements:
            text = str(el).strip()
            if not text:
                continue
            if el.category == "Title":
                parts.append(f"\n\n{text}")
            elif el.category == "Table":
                parts.append(f"\n{text}\n")
            else:
                parts.append(text)

        full_text = "\n".join(parts).strip()

        return ParsedDocument(
            text=full_text,
            title=title,
            metadata={"source": file_path, "element_count": len(elements)},
        )

    def supports(self, mime_type: str) -> bool:
        return mime_type in _SUPPORTED_MIME_TYPES

    async def health_check(self) -> HealthStatus:
        try:
            import unstructured  # noqa: F401
            return HealthStatus.HEALTHY
        except ImportError:
            return HealthStatus.UNHEALTHY
