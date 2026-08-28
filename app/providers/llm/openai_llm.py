from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.events import DomainEvent, EventType, event_bus
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, HealthStatus, LLMProvider

logger = logging.getLogger(__name__)


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
        "deepseek-v4-flash":   1_000_000,
        "gpt-4o":              128_000,
        "gpt-4o-mini":         128_000,
        "gpt-4-turbo":         128_000,
        "gpt-4":                 8_192,
        "gpt-3.5-turbo":        16_385,
        "gpt-3.5-turbo-16k":    16_385,
    }

    def get_context_window(self) -> int:
        model = self._default_model.lower()
        for prefix, size in self._CONTEXT_WINDOWS.items():
            if model.startswith(prefix):
                return size
        # 从模型名猜测 context window 大小
        if "128k" in model:
            return 128_000
        if "32k" in model:
            return 32_000
        if "16k" in model:
            return 16_385
        return 32_000  # 现代模型通用保守下限（替代原来的 8192）

    async def _publish_usage(
        self,
        ctx: ProcessingContext,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        try:
            await event_bus.publish(DomainEvent(
                type=EventType.TOKEN_CONSUMED,
                tenant_id=ctx.tenant_id,
                payload={
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            ))
        except Exception as e:
            logger.warning("Failed to publish TOKEN_CONSUMED event: %s", e)

    async def generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> str:
        model = self._resolve_model(ctx)
        resp = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            **kwargs,
        )
        if resp.usage:
            await self._publish_usage(
                ctx, model,
                resp.usage.prompt_tokens,
                resp.usage.completion_tokens,
            )
        return resp.choices[0].message.content or ""

    async def stream_generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[str]:
        model = self._resolve_model(ctx)
        stream = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            stream=True,
            stream_options={"include_usage": True},
            **kwargs,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            elif chunk.usage:
                # 最后一个 chunk 携带 usage，不 yield
                await self._publish_usage(
                    ctx, model,
                    chunk.usage.prompt_tokens,
                    chunk.usage.completion_tokens,
                )
