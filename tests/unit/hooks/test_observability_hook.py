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


from app.domain.retrieval import RetrievalQuery, SearchContext, SearchHit


class _Vectorish(BaseStage[SearchContext, SearchContext]):
    name = "vector_search"  # 与真实 stage 同名,触发 RetrievalExtractor

    async def _execute(self, ctx: ProcessingContext, input: SearchContext) -> SearchContext:
        input.vector_hits.append(
            SearchHit(chunk_id="c1", document_id="d1", chunk_index=0, score=0.9, source="vector")
        )
        return input


async def test_extractor_attrs_land_on_stage_span(fake_ctx, span_exporter):
    """T6:extractor 翻译出的属性必须真的挂到对应 stage span 上。"""
    sc = SearchContext(query=RetrievalQuery(query_text="q", tenant_id="t"))
    pipeline = Pipeline(stages=[_Vectorish()], hooks=[ObservabilityHook()])
    await pipeline.run(fake_ctx, sc)

    span = {s.name: s for s in span_exporter.get_finished_spans()}["stage.vector_search"]
    assert span.attributes["openinference.span.kind"] == "RETRIEVER"
    assert span.attributes["retrieval.documents.0.document.id"] == "c1"


async def test_exploding_extractor_does_not_break_pipeline(fake_ctx, span_exporter, monkeypatch):
    """观测铁律:翻译官抛异常,业务管线照常出结果。"""
    import app.pipeline.hooks.observability_hook as hook_mod

    class _Bomb:
        def matches(self, stage_name, payload):
            raise RuntimeError("boom")

        def extract(self, stage_name, payload):
            return {}

    monkeypatch.setattr(hook_mod, "EXTRACTORS", [_Bomb()])
    pipeline = Pipeline(stages=[_EchoA()], hooks=[ObservabilityHook()])
    result = await pipeline.run(fake_ctx, "ok")
    assert result == "ok"  # 业务无感
