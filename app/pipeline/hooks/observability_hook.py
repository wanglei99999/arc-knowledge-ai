from __future__ import annotations

import logging
import time

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.trace import StatusCode

from app.infrastructure.telemetry.extractors import EXTRACTORS
from app.infrastructure.telemetry.metrics import PIPELINE_TOTAL, STAGE_DURATION
from app.infrastructure.telemetry.otel import get_tracer
from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase

logger = logging.getLogger(__name__)

_tracer = get_tracer("arc.pipeline")

# ctx.metadata 里的 key
_PIPELINE_SPAN_KEY = "_obs_pipeline_span"
_PIPELINE_START_KEY = "_obs_pipeline_start"
_PIPELINE_TOKEN_KEY = "_obs_pipeline_ctxtoken"
_STAGE_SPAN_KEY = "_obs_span_{}"
_STAGE_START_KEY = "_obs_start_{}"
_STAGE_TOKEN_KEY = "_obs_ctxtoken_{}"


class ObservabilityHook(BaseHook):
    """
    全 Phase Hook：OTel Span + Prometheus metrics + 耗时日志。

    PRE_PIPELINE：  开启 Pipeline Span，记录开始时间
    PRE_STAGE：     开启 Stage Span，记录开始时间
    POST_STAGE：    结束 Stage Span，记录 Histogram，打印耗时日志
    POST_PIPELINE： 结束 Pipeline Span，累加 Pipeline counter
    ON_ERROR：      标记 Span 为 ERROR 状态，打印错误日志
    """

    phase = [
        Phase.PRE_PIPELINE,
        Phase.PRE_STAGE,
        Phase.POST_STAGE,
        Phase.POST_PIPELINE,
        Phase.ON_ERROR,
    ]
    priority = 100  # 最后执行，不干扰业务 Hook

    async def handle(self, event: HookEvent) -> HookResult:
        # 观测铁律:本 Hook 任何内部失败(tracer/metrics/日志)都不许影响业务管线。
        # 只有观测 Hook 自我免责——Guard 类 Hook 抛错/ABORT 是它们的本职,不在此列。
        try:
            return await self._handle(event)
        except Exception:
            logger.warning(
                "observability hook failed (phase=%s), business continues",
                event.phase,
                exc_info=True,
            )
            return HookResult.CONTINUE

    @staticmethod
    def _run_extractors(span: otel_trace.Span, stage_name: str, payload: object | None) -> None:
        """内容收割:extractor 认领 payload 并翻译成 span 属性。失败吞掉记日志。"""
        if payload is None:
            return
        for extractor in EXTRACTORS:
            try:
                if extractor.matches(stage_name, payload):
                    span.set_attributes(extractor.extract(stage_name, payload))
            except Exception:
                logger.warning("content extractor failed on stage %s", stage_name, exc_info=True)

    async def _handle(self, event: HookEvent) -> HookResult:
        ctx = event.ctx

        if event.phase == Phase.PRE_PIPELINE:
            span = _tracer.start_span(
                "pipeline.run",
                attributes={
                    "tenant_id": ctx.tenant_id,
                    "task_id": ctx.task_id,
                    "trace_id": ctx.trace_id,
                },
            )
            ctx.metadata[_PIPELINE_SPAN_KEY] = span
            ctx.metadata[_PIPELINE_START_KEY] = time.monotonic()
            # 激活为当前上下文:pipeline 内所有 span(stage、stage 内的 LLM 调用)自动认树。
            # 管线是同一任务内的顺序 await,无 yield 泄漏风险;POST_PIPELINE/ON_ERROR 处 detach。
            ctx.metadata[_PIPELINE_TOKEN_KEY] = otel_context.attach(
                otel_trace.set_span_in_context(span)
            )
            logger.info(
                "[Pipeline] START  tenant=%s task=%s trace=%s",
                ctx.tenant_id,
                ctx.task_id,
                ctx.trace_id,
            )

        elif event.phase == Phase.PRE_STAGE and event.stage:
            # 显式认爹:start_span 不会自动挂父子,必须把 pipeline span 递进去,
            # 否则 stage span 全是孤儿,trace 后端里不成树。
            parent_span = ctx.metadata.get(_PIPELINE_SPAN_KEY)
            parent_context = (
                otel_trace.set_span_in_context(parent_span) if parent_span is not None else None
            )
            span = _tracer.start_span(
                f"stage.{event.stage.name}",
                context=parent_context,
                attributes={
                    "stage": event.stage.name,
                    "tenant_id": ctx.tenant_id,
                    "task_id": ctx.task_id,
                },
            )
            ctx.metadata[_STAGE_SPAN_KEY.format(event.stage.name)] = span
            ctx.metadata[_STAGE_START_KEY.format(event.stage.name)] = time.monotonic()
            # 激活 stage span:stage 内部的 llm.generate 等子 span 才能挂到它下面
            ctx.metadata[_STAGE_TOKEN_KEY.format(event.stage.name)] = otel_context.attach(
                otel_trace.set_span_in_context(span)
            )

        elif event.phase == Phase.POST_STAGE and event.stage:
            token = ctx.metadata.pop(_STAGE_TOKEN_KEY.format(event.stage.name), None)
            if token is not None:
                otel_context.detach(token)
            start = ctx.metadata.get(_STAGE_START_KEY.format(event.stage.name), time.monotonic())
            elapsed_s = time.monotonic() - start
            elapsed_ms = elapsed_s * 1000

            # Prometheus
            STAGE_DURATION.labels(
                stage=event.stage.name,
                tenant_id=ctx.tenant_id,
            ).observe(elapsed_s)

            # OTel Span
            span: otel_trace.Span | None = ctx.metadata.pop(
                _STAGE_SPAN_KEY.format(event.stage.name), None
            )
            if span is not None:
                self._run_extractors(span, event.stage.name, event.payload)
                span.end()

            logger.info(
                "[Stage]    OK     stage=%-24s elapsed=%.1fms  tenant=%s task=%s",
                event.stage.name,
                elapsed_ms,
                ctx.tenant_id,
                ctx.task_id,
            )

        elif event.phase == Phase.POST_PIPELINE:
            start = ctx.metadata.get(_PIPELINE_START_KEY, time.monotonic())
            elapsed_ms = (time.monotonic() - start) * 1000

            # Prometheus
            PIPELINE_TOTAL.labels(status="success", tenant_id=ctx.tenant_id).inc()

            # OTel Span:先还原上下文,再结束 span
            token = ctx.metadata.pop(_PIPELINE_TOKEN_KEY, None)
            if token is not None:
                otel_context.detach(token)
            span = ctx.metadata.pop(_PIPELINE_SPAN_KEY, None)
            if span is not None:
                span.end()

            logger.info(
                "[Pipeline] DONE   elapsed=%.1fms  tenant=%s task=%s",
                elapsed_ms,
                ctx.tenant_id,
                ctx.task_id,
            )

        elif event.phase == Phase.ON_ERROR:
            stage_name = event.stage.name if event.stage else "unknown"

            # Prometheus
            PIPELINE_TOTAL.labels(status="error", tenant_id=ctx.tenant_id).inc()

            # 还原上下文(LIFO:先 stage 后 pipeline),再结束 span
            stage_token = ctx.metadata.pop(_STAGE_TOKEN_KEY.format(stage_name), None)
            if stage_token is not None:
                otel_context.detach(stage_token)
            pipeline_token = ctx.metadata.pop(_PIPELINE_TOKEN_KEY, None)
            if pipeline_token is not None:
                otel_context.detach(pipeline_token)

            # 结束 Stage Span（标记为 ERROR）
            span = ctx.metadata.pop(_STAGE_SPAN_KEY.format(stage_name), None)
            if span is not None:
                span.set_status(StatusCode.ERROR, str(event.error))
                span.end()

            # 结束 Pipeline Span（标记为 ERROR）
            pipeline_span = ctx.metadata.pop(_PIPELINE_SPAN_KEY, None)
            if pipeline_span is not None:
                pipeline_span.set_status(StatusCode.ERROR, str(event.error))
                pipeline_span.end()

            logger.error(
                "[Pipeline] ERROR  stage=%s error=%s  tenant=%s task=%s",
                stage_name,
                event.error,
                ctx.tenant_id,
                ctx.task_id,
                exc_info=event.error,
            )

        return HookResult.CONTINUE
