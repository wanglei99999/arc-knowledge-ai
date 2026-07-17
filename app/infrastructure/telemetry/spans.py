from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry.trace import Span

from app.infrastructure.telemetry.otel import get_tracer

_tracer = get_tracer("arc.rag")


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
