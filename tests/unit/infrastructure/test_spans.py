from __future__ import annotations

import pytest

from app.infrastructure.telemetry.spans import traced_block


def test_traced_block_records_span_with_attrs(span_exporter):
    with traced_block("cache.lookup", **{"arc.cache.hit": True}):
        pass
    span = span_exporter.get_finished_spans()[0]
    assert span.name == "cache.lookup"
    assert span.attributes["arc.cache.hit"] is True


def test_traced_block_nests(span_exporter):
    with traced_block("outer"):
        with traced_block("inner"):
            pass
    spans = {s.name: s for s in span_exporter.get_finished_spans()}
    assert spans["inner"].parent.span_id == spans["outer"].context.span_id


def test_traced_block_does_not_swallow_exception(span_exporter):
    with pytest.raises(ValueError):
        with traced_block("boom"):
            raise ValueError("x")
    assert span_exporter.get_finished_spans()[0].name == "boom"  # span 仍正常结束
