from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span

from app.infrastructure.telemetry.otel import get_tracer

_tracer = get_tracer("arc.rag")


@contextmanager
def activate_span(span: Span) -> Iterator[Span]:
    """
    把已创建的 span 临时设为当前上下文，块内新建的 span 自动认它当父。

    纪律：绝不允许跨 async generator 的 yield 持有（会把上下文泄漏给消费方），
    只包"同一任务内的顺序 await 段"。
    """
    token = otel_context.attach(otel_trace.set_span_in_context(span))
    try:
        yield span
    finally:
        otel_context.detach(token)


@contextmanager
def traced_block(name: str, **attributes: Any) -> Iterator[Span]:
    """
    手动埋点便捷入口：with traced_block("cache.lookup", **{"arc.cache.hit": True}) as span
    异常照常上抛（start_as_current_span 自动记 ERROR 状态并结束 span）。
    """
    with _tracer.start_as_current_span(name) as span:
        if attributes:
            span.set_attributes(attributes)
        yield span
