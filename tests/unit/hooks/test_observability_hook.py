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


from app.providers.base import ChatMessage, HealthStatus, LLMProvider
from app.providers.llm.traced_llm import TracedLLMProvider


class _MiniLLM(LLMProvider):
    provider_id = "mini_llm"

    async def health_check(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def generate(self, ctx, messages, **kwargs) -> str:
        return "ok"

    async def stream_generate(self, ctx, messages, **kwargs):
        yield "ok"


class _LLMStage(BaseStage[object, object]):
    name = "llm_stage"

    async def _execute(self, ctx: ProcessingContext, input: object) -> object:
        provider = TracedLLMProvider(_MiniLLM())
        return await provider.generate(ctx, [ChatMessage(role="user", content="hi")])


async def test_llm_span_nests_under_stage_span(fake_ctx, span_exporter):
    """修复回归:stage span 激活为 current 后,stage 内的 llm.generate 必须挂到它下面。"""
    pipeline = Pipeline(stages=[_LLMStage()], hooks=[ObservabilityHook()])
    await pipeline.run(fake_ctx, None)

    spans = {s.name: s for s in span_exporter.get_finished_spans()}
    assert spans["llm.generate"].parent.span_id == spans["stage.llm_stage"].context.span_id
    assert spans["stage.llm_stage"].parent.span_id == spans["pipeline.run"].context.span_id


async def test_hook_internal_failure_does_not_break_pipeline(fake_ctx, span_exporter, monkeypatch):
    """观测铁律回归:tracer 本身坏掉(extractor 之外的环节),业务管线照常出结果。"""
    import app.pipeline.hooks.observability_hook as hook_mod

    class _BrokenTracer:
        def start_span(self, *args, **kwargs):
            raise RuntimeError("tracer down")

    monkeypatch.setattr(hook_mod, "_tracer", _BrokenTracer())
    pipeline = Pipeline(stages=[_EchoA()], hooks=[ObservabilityHook()])
    result = await pipeline.run(fake_ctx, "ok")
    assert result == "ok"


async def test_context_restored_after_pipeline(fake_ctx, span_exporter):
    """防泄漏回归:pipeline 跑完后,当前上下文必须还原(不残留任何 span)。"""
    from opentelemetry import trace as _trace

    pipeline = Pipeline(stages=[_EchoA()], hooks=[ObservabilityHook()])
    await pipeline.run(fake_ctx, "x")
    assert not _trace.get_current_span().get_span_context().is_valid
