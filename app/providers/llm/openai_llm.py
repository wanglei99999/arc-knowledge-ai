from __future__ import annotations

from typing import AsyncIterator

from openai import AsyncOpenAI

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, HealthStatus, LLMProvider


@registry.provider("openai_llm")
class OpenAILLMProvider(LLMProvider):
    """OpenAI Chat Completions，支持流式和非流式生成。"""

    provider_id = "openai_llm"

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        self._default_model = settings.openai_llm_model

    def _resolve_model(self, ctx: ProcessingContext) -> str:
        """三层优先级：ctx.config.default_llm_model > settings.openai_llm_model"""
        return ctx.config.default_llm_model or self._default_model

    async def health_check(self) -> HealthStatus:
        try:
            await self._client.models.list()
            return HealthStatus.HEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY

    # model → context_window 映射（常见 OpenAI 模型）
    _CONTEXT_WINDOWS: dict[str, int] = {
        "gpt-4o":              128_000,
        "gpt-4o-mini":         128_000,
        "gpt-4-turbo":         128_000,
        "gpt-4":                 8_192,
        "gpt-3.5-turbo":        16_385,
        "gpt-3.5-turbo-16k":    16_385,
    }

    def get_context_window(self) -> int:
        for prefix, size in self._CONTEXT_WINDOWS.items():
            if self._default_model.startswith(prefix):
                return size
        return 8_192  # 未知模型保守默认

    async def generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> str:
        resp = await self._client.chat.completions.create(
            model=self._resolve_model(ctx),
            messages=[{"role": m.role, "content": m.content} for m in messages],
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    async def stream_generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._resolve_model(ctx),
            messages=[{"role": m.role, "content": m.content} for m in messages],
            stream=True,
            **kwargs,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
