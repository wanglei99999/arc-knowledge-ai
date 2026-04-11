from __future__ import annotations

from app.domain.document import RawFile
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage
from app.providers.base import ParsedDocument, ParserProvider


class ParserStage(BaseStage[RawFile, ParsedDocument]):
    """
    通用文档解析 Stage。

    根据租户配置选择 ParserProvider，将原始文件转换为结构化文本。
    支持 PDF、Word、Excel、HTML、Markdown 等（取决于 Provider 实现）。
    """

    name = "parser"
    produces = frozenset({"parsed_title"})

    def __init__(self, provider: ParserProvider | None = None) -> None:
        # 允许外部注入（测试 mock 用），不传则运行时从 registry 取
        self._provider = provider

    def _get_provider(self, ctx: ProcessingContext) -> ParserProvider:
        if self._provider is not None:
            return self._provider
        # 从租户配置读 provider_id，默认 unstructured_parser
        provider_id = getattr(ctx.config, "parser_provider", "unstructured_parser")
        return registry.get_provider(provider_id)  # type: ignore[return-value]

    async def _execute(
        self,
        ctx: ProcessingContext,
        input: RawFile,
    ) -> ParsedDocument:
        provider = self._get_provider(ctx)
        result = await provider.parse(ctx, input.file_path)

        # 将 title 写入 context，供后续 Stage 使用（可选）
        if result.title:
            ctx.metadata["parsed_title"] = result.title

        return result
