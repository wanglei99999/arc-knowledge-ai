"""ObservabilityHook 的 span 树形测试。

缺陷回归:start_span 不自动挂父子,stage span 必须显式认 pipeline span 当爹,
否则 Phoenix 里看到的不是树,是散落的兄弟 span。
"""

from __future__ import annotations

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.pipeline import Pipeline
from app.pipeline.core.stage import BaseStage
from app.pipeline.hooks.observability_hook import ObservabilityHook


class _EchoA(BaseStage[object, object]):
    name = "echo_a"

    async def _execute(self, ctx: ProcessingContext, input: object) -> object:
        return input


class _EchoB(BaseStage[object, object]):
    name = "echo_b"

    async def _execute(self, ctx: ProcessingContext, input: object) -> object:
        return input


async def test_stage_spans_are_children_of_pipeline_span(fake_ctx, span_exporter):
    """缺陷1回归:stage span 必须是 pipeline span 的孩子,同属一条 trace。"""
    pipeline = Pipeline(stages=[_EchoA(), _EchoB()], hooks=[ObservabilityHook()])
    await pipeline.run(fake_ctx, "x")

    spans = {s.name: s for s in span_exporter.get_finished_spans()}
    assert set(spans) == {"pipeline.run", "stage.echo_a", "stage.echo_b"}

    root = spans["pipeline.run"]
    for name in ("stage.echo_a", "stage.echo_b"):
        assert spans[name].parent is not None, f"{name} 没有父 span"
        assert spans[name].parent.span_id == root.context.span_id
        assert spans[name].context.trace_id == root.context.trace_id
