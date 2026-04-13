from __future__ import annotations

import logging
import time

from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase

logger = logging.getLogger(__name__)

_START_KEY = "_obs_start_{}"   # ctx.metadata 里存各 Stage 的开始时间
_PIPELINE_START_KEY = "_obs_pipeline_start"


class ObservabilityHook(BaseHook):
    """
    PRE_STAGE / POST_STAGE / POST_PIPELINE / ON_ERROR Hook：耗时记录与日志。

    记录每个 Stage 的执行耗时，Pipeline 整体耗时，以及错误信息。
    Phase 4 可对接 OpenTelemetry Span / Prometheus Histogram。
    """

    phase = [
        Phase.PRE_PIPELINE,
        Phase.PRE_STAGE,
        Phase.POST_STAGE,
        Phase.POST_PIPELINE,
        Phase.ON_ERROR,
    ]
    priority = 100   # 最后执行，不干扰业务 Hook

    async def handle(self, event: HookEvent) -> HookResult:
        ctx = event.ctx

        if event.phase == Phase.PRE_PIPELINE:
            ctx.metadata[_PIPELINE_START_KEY] = time.monotonic()
            logger.info(
                "[Pipeline] START  tenant=%s task=%s trace=%s",
                ctx.tenant_id,
                ctx.task_id,
                ctx.trace_id,
            )

        elif event.phase == Phase.PRE_STAGE and event.stage:
            ctx.metadata[_START_KEY.format(event.stage.name)] = time.monotonic()

        elif event.phase == Phase.POST_STAGE and event.stage:
            start = ctx.metadata.get(_START_KEY.format(event.stage.name), time.monotonic())
            elapsed_ms = (time.monotonic() - start) * 1000
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
            logger.info(
                "[Pipeline] DONE   elapsed=%.1fms  tenant=%s task=%s",
                elapsed_ms,
                ctx.tenant_id,
                ctx.task_id,
            )

        elif event.phase == Phase.ON_ERROR:
            stage_name = event.stage.name if event.stage else "unknown"
            logger.error(
                "[Pipeline] ERROR  stage=%s error=%s  tenant=%s task=%s",
                stage_name,
                event.error,
                ctx.tenant_id,
                ctx.task_id,
                exc_info=event.error,
            )

        return HookResult.CONTINUE
