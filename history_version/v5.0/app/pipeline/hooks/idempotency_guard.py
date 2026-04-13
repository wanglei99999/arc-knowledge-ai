from __future__ import annotations

import logging

from app.infrastructure.redis.client import get_redis
from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase

logger = logging.getLogger(__name__)

# Redis key TTL：24 小时（Temporal Activity 重试窗口内有效）
_TTL_SECONDS = 86_400

# Redis key 格式：idempotency:{task_id}:{stage_name}
_KEY_PREFIX = "idempotency"


def _key(task_id: str, stage_name: str) -> str:
    return f"{_KEY_PREFIX}:{task_id}:{stage_name}"


class IdempotencyGuard(BaseHook):
    """
    PRE_STAGE + POST_STAGE Hook：基于 Redis 的 Stage 级幂等保护。

    PRE_STAGE：检查该 Stage 是否已成功执行 → 若是，返回 SKIP_STAGE。
    POST_STAGE：标记该 Stage 已成功，写入 Redis（TTL 24h）。

    适用场景：Temporal Activity 因超时重试时，已完成的 Stage 不重复执行。
    """

    phase = [Phase.PRE_STAGE, Phase.POST_STAGE]
    priority = 30

    async def handle(self, event: HookEvent) -> HookResult:
        if event.stage is None:
            return HookResult.CONTINUE

        task_id = event.ctx.task_id
        stage_name = event.stage.name
        redis = get_redis()

        if event.phase == Phase.PRE_STAGE:
            exists = await redis.exists(_key(task_id, stage_name))
            if exists:
                logger.info(
                    "Skipping stage %r (task=%s): already completed",
                    stage_name,
                    task_id,
                )
                return HookResult.SKIP_STAGE

        elif event.phase == Phase.POST_STAGE:
            await redis.setex(_key(task_id, stage_name), _TTL_SECONDS, "1")
            logger.debug(
                "Marked stage %r as completed (task=%s)",
                stage_name,
                task_id,
            )

        return HookResult.CONTINUE
