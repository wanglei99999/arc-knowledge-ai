from __future__ import annotations

import time
from contextlib import aclosing
from typing import Any, AsyncIterator

from opentelemetry.trace import Span, StatusCode

from app.config.settings import settings
from app.infrastructure.telemetry.extractors import KIND_KEY
from app.infrastructure.telemetry.otel import get_tracer
from app.pipeline.core.context import ProcessingContext
from app.providers.base import ChatMessage, HealthStatus, LLMProvider
from app.utils.tokenizer import count_tokens

_tracer = get_tracer("arc.llm")

# 编排层预计算的 prompt token 数经 ctx.metadata 递入,避免对同一 prompt 二次编码
PROMPT_TOKENS_KEY = "_llm_prompt_tokens"


class TracedLLMProvider(LLMProvider):
    """
    LLM 观测包装层（照 ResilientLLMProvider 的先例，装饰器模式）。

    只做记录，不改行为：token 流无损透传，异常原样上抛。
    包在最外层（Traced(Resilient(primary))），熔断降级后的最终结果被如实记录。
    """

    provider_id = "traced_llm"

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    async def health_check(self) -> HealthStatus:
        return await self._inner.health_check()

    def get_context_window(self) -> int:
        return self._inner.get_context_window()

    @staticmethod
    def _base_attrs(ctx: ProcessingContext, messages: list[ChatMessage]) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            KIND_KEY: "LLM",
            "llm.model_name": ctx.config.default_llm_model or "",
            "arc.tenant_id": ctx.tenant_id,
        }
        if settings.otel_capture_content:
            for i, m in enumerate(messages):
                attrs[f"llm.input_messages.{i}.message.role"] = m.role
                attrs[f"llm.input_messages.{i}.message.content"] = m.content
        return attrs

    @staticmethod
    def _finish_attrs(
        span: Span, ctx: ProcessingContext, messages: list[ChatMessage], completion: str
    ) -> None:
        # 编排层若已算过 prompt token(预算计算的副产品),直接复用;
        # 没有(如 query 改写这类短 prompt 场景)才自己编码。
        prompt_tokens = ctx.metadata.pop(PROMPT_TOKENS_KEY, None)
        if prompt_tokens is None:
            prompt_tokens = sum(count_tokens(m.content) for m in messages)
        completion_tokens = count_tokens(completion)
        if settings.otel_capture_content:
            span.set_attribute("llm.output_messages.0.message.content", completion)
        span.set_attribute("llm.token_count.prompt", prompt_tokens)
        span.set_attribute("llm.token_count.completion", completion_tokens)
        span.set_attribute("llm.token_count.total", prompt_tokens + completion_tokens)

    async def generate(
        self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs: Any
    ) -> str:
        with _tracer.start_as_current_span(
            "llm.generate", attributes=self._base_attrs(ctx, messages)
        ) as span:
            result = await self._inner.generate(ctx, messages, **kwargs)
            self._finish_attrs(span, ctx, messages, result)
            return result

    async def stream_generate(
        self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        span = _tracer.start_span("llm.generate", attributes=self._base_attrs(ctx, messages))
        start = time.monotonic()
        first = True
        chunks: list[str] = []
        try:
            # aclosing:上游中途放弃时内层流被确定性关闭,不等 GC
            async with aclosing(self._inner.stream_generate(ctx, messages, **kwargs)) as inner:
                async for token in inner:
                    if first:
                        span.set_attribute(
                            "arc.llm.time_to_first_token_ms", (time.monotonic() - start) * 1000
                        )
                        first = False
                    chunks.append(token)
                    yield token
        except BaseException as e:
            # BaseException 才接得住 GeneratorExit(客户端断开)/CancelledError,
            # 否则中断的流会被记成"成功"——遥测静默失真。异常原样上抛。
            span.set_status(StatusCode.ERROR, str(e))
            raise
        finally:
            self._finish_attrs(span, ctx, messages, "".join(chunks))
            span.end()
