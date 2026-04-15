from __future__ import annotations

import logging
from typing import AsyncIterator

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, HealthStatus, LLMProvider

logger = logging.getLogger(__name__)


def _make_retry():
    """构造 tenacity retry 装饰器，参数来自 settings。"""
    return retry(
        stop=stop_after_attempt(settings.llm_max_retries),
        wait=wait_exponential(
            multiplier=1,
            min=settings.llm_retry_min_wait,
            max=settings.llm_retry_max_wait,
        ),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )


class ResilientLLMProvider(LLMProvider):
    """
    熔断 / 降级包装器。

    generate()：tenacity 重试，最终失败后自动切换 fallback Provider。
    stream_generate()：主 Provider 失败（首个 token 前）自动切换 fallback；
                       已开始推送 token 后失败则直接抛出（无法回滚）。
    """

    provider_id = "resilient_llm"

    def __init__(self, primary: LLMProvider, fallback_provider_id: str) -> None:
        self._primary = primary
        self._fallback_id = fallback_provider_id

    async def health_check(self) -> HealthStatus:
        return await self._primary.health_check()

    async def generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> str:
        @_make_retry()
        async def _call() -> str:
            return await self._primary.generate(ctx, messages, **kwargs)

        try:
            return await _call()
        except Exception as exc:
            logger.warning(
                "Primary LLM %r failed after %d retries, falling back to %r: %s",
                self._primary.provider_id,
                settings.llm_max_retries,
                self._fallback_id,
                exc,
            )
            fallback: LLMProvider = registry.get_provider(self._fallback_id)  # type: ignore[assignment]
            return await fallback.generate(ctx, messages, **kwargs)

    async def stream_generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[str]:
        tokens_yielded = 0
        use_fallback = False

        try:
            async for token in self._primary.stream_generate(ctx, messages, **kwargs):
                tokens_yielded += 1
                yield token
        except Exception as exc:
            if tokens_yielded > 0:
                # 已推送部分 token，无法回滚，直接报错
                logger.error(
                    "Primary LLM stream failed after %d tokens, cannot fall back: %s",
                    tokens_yielded,
                    exc,
                )
                raise
            logger.warning(
                "Primary LLM %r stream failed before first token, falling back to %r: %s",
                self._primary.provider_id,
                self._fallback_id,
                exc,
            )
            use_fallback = True

        if use_fallback:
            fallback: LLMProvider = registry.get_provider(self._fallback_id)  # type: ignore[assignment]
            async for token in fallback.stream_generate(ctx, messages, **kwargs):
                yield token
