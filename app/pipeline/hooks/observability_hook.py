from __future__ import annotations

import logging
import time

from opentelemetry import trace as otel_trace
from opentelemetry.trace import StatusCode

from app.infrastructure.telemetry.metrics import PIPELINE_TOTAL, STAGE_DURATION
from app.infrastructure.telemetry.otel import get_tracer
from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase

logger = logging.getLogger(__name__)

_tracer = get_tracer("arc.pipeline")

# ctx.metadata 里的 key
_PIPELINE_SPAN_KEY = "_obs_pipeline_span"
_PIPELINE_START_KEY = "_obs_pipeline_start"
_STAGE_SPAN_KEY = "_obs_span_{}"
_STAGE_START_KEY = "_obs_start_{}"


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

        elif event.phase == Phase.POST_STAGE and event.stage:
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

            # OTel Span
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
