from __future__ import annotations

from typing import AsyncIterator

import pytest

from app.providers.base import ChatMessage, HealthStatus, LLMProvider
from app.providers.llm.traced_llm import TracedLLMProvider


class _FakeLLM(LLMProvider):
    provider_id = "fake_llm"

    async def health_check(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def generate(self, ctx, messages, **kwargs) -> str:
        return "四十二"

    async def stream_generate(self, ctx, messages, **kwargs) -> AsyncIterator[str]:
        for t in ["四", "十", "二"]:
            yield t


_MSGS = [ChatMessage(role="user", content="答案是什么")]


async def test_generate_records_llm_span(fake_ctx, span_exporter):
    result = await TracedLLMProvider(_FakeLLM()).generate(fake_ctx, _MSGS)
    assert result == "四十二"  # 结果无损
    spans = [s for s in span_exporter.get_finished_spans() if s.name == "llm.generate"]
    assert len(spans) == 1
    a = spans[0].attributes
    assert a["openinference.span.kind"] == "LLM"
    assert a["llm.input_messages.0.message.role"] == "user"
    assert a["llm.output_messages.0.message.content"] == "四十二"
    assert a["llm.token_count.total"] == a["llm.token_count.prompt"] + a["llm.token_count.completion"]


async def test_stream_passthrough_lossless_and_ttft(fake_ctx, span_exporter):
    tokens = [t async for t in TracedLLMProvider(_FakeLLM()).stream_generate(fake_ctx, _MSGS)]
    assert tokens == ["四", "十", "二"]  # 逐 token 无损透传
    span = [s for s in span_exporter.get_finished_spans() if s.name == "llm.generate"][0]
    assert span.attributes["arc.llm.time_to_first_token_ms"] >= 0
    assert span.attributes["llm.output_messages.0.message.content"] == "四十二"


async def test_stream_error_propagates_and_span_ends(fake_ctx, span_exporter):
    class _Boom(_FakeLLM):
        async def stream_generate(self, ctx, messages, **kwargs) -> AsyncIterator[str]:
            yield "四"
            raise RuntimeError("connection lost")

    with pytest.raises(RuntimeError):
        async for _ in TracedLLMProvider(_Boom()).stream_generate(fake_ctx, _MSGS):
            pass
    span = [s for s in span_exporter.get_finished_spans() if s.name == "llm.generate"][0]
    from opentelemetry.trace import StatusCode

    assert span.status.status_code == StatusCode.ERROR  # 记错误但不吞异常


async def test_precomputed_prompt_tokens_reused(fake_ctx, span_exporter):
    """编排层已算过的 prompt token 数直接消费,不对同一 prompt 二次编码。"""
    from app.providers.llm.traced_llm import PROMPT_TOKENS_KEY

    fake_ctx.metadata[PROMPT_TOKENS_KEY] = 12345
    await TracedLLMProvider(_FakeLLM()).generate(fake_ctx, _MSGS)
    span = [s for s in span_exporter.get_finished_spans() if s.name == "llm.generate"][0]
    assert span.attributes["llm.token_count.prompt"] == 12345
    assert PROMPT_TOKENS_KEY not in fake_ctx.metadata  # pop 消费,不残留污染下次调用


async def test_stream_abort_marks_error(fake_ctx, span_exporter):
    """客户端断开(aclose→GeneratorExit)不能被记成成功——BaseException 才接得住。"""
    from opentelemetry.trace import StatusCode

    agen = TracedLLMProvider(_FakeLLM()).stream_generate(fake_ctx, _MSGS)
    assert await anext(agen) == "四"
    await agen.aclose()  # 模拟 SSE 客户端中途断开

    spans = [s for s in span_exporter.get_finished_spans() if s.name == "llm.generate"]
    assert len(spans) == 1  # span 立即闭合,不等 GC
    assert spans[0].status.status_code == StatusCode.ERROR
