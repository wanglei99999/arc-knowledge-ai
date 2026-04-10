from __future__ import annotations

import dataclasses

from app.domain.document import DocumentChunk
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage
from app.providers.base import EmbeddingProvider

# OpenAI 单次最多 2048 条，保守取 100
_DEFAULT_BATCH_SIZE = 100


class EmbedStage(BaseStage[list[DocumentChunk], list[DocumentChunk]]):
    """
    批量 Embedding Stage。

    将 DocumentChunk.content 转换为向量，填充到 chunk.embedding。
    支持分批处理，避免单次调用超出 Provider 限制。
    """

    name = "embedder"
    requires = frozenset()
    produces = frozenset({"embedding_model", "embedding_dimension"})

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self._provider = provider
        self._batch_size = batch_size

    def _get_provider(self, ctx: ProcessingContext) -> EmbeddingProvider:
        if self._provider is not None:
            return self._provider
        return registry.get_provider(ctx.config.embedding_provider)  # type: ignore[return-value]

    async def _execute(
        self,
        ctx: ProcessingContext,
        input: list[DocumentChunk],
    ) -> list[DocumentChunk]:
        if not input:
            return []

        provider = self._get_provider(ctx)
        result: list[DocumentChunk] = []

        for i in range(0, len(input), self._batch_size):
            batch = input[i : i + self._batch_size]
            texts = [chunk.content for chunk in batch]
            embeddings = await provider.embed(ctx, texts)

            for chunk, vec in zip(batch, embeddings):
                result.append(dataclasses.replace(chunk, embedding=vec))

        # 将模型信息写入 context，供后续 Stage（写 Milvus）使用
        ctx.metadata["embedding_model"] = provider.get_model_name()
        ctx.metadata["embedding_dimension"] = provider.get_dimension()

        return result
