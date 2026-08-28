from __future__ import annotations

from openai import AsyncOpenAI

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import EmbeddingProvider, HealthStatus


@registry.provider("openai_embedding")
class OpenAIEmbeddingProvider(EmbeddingProvider):
    """
    OpenAI Embedding Provider。

    支持 text-embedding-3-small（默认）/ text-embedding-3-large / ada-002。
    API Key 从环境变量 OPENAI_API_KEY 读取，也可构造时传入。
    """

    provider_id = "openai_embedding"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        # embedding 服务可单独部署：优先读 embedding 专用配置，留空则复用 LLM 配置
        effective_base_url = settings.openai_embedding_base_url or settings.openai_base_url
        effective_api_key = api_key or settings.openai_embedding_api_key or settings.openai_api_key
        self._client = AsyncOpenAI(
            api_key=effective_api_key,
            base_url=effective_base_url,
        )
        self._model = model or settings.openai_embedding_model

    async def embed(
        self,
        ctx: ProcessingContext,
        texts: list[str],
    ) -> list[list[float]]:
        if not texts:
            return []

        batch_size = settings.openai_embedding_batch_size
        # bge 系列 max 512 tokens；CJK 约 1 char/token，截断至 450 字符留出特殊 token 余量
        max_chars = 450
        texts = [t[:max_chars] if len(t) > max_chars else t for t in texts]

        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await self._client.embeddings.create(
                model=self._model,
                input=batch,
                dimensions=settings.openai_embedding_dimensions,
            )
            sorted_data = sorted(response.data, key=lambda x: x.index)
            results.extend(item.embedding for item in sorted_data)
        return results

    def get_dimension(self) -> int:
        return settings.openai_embedding_dimensions

    def get_model_name(self) -> str:
        return self._model

    async def health_check(self) -> HealthStatus:
        try:
            await self._client.models.retrieve(self._model)
            return HealthStatus.HEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY
