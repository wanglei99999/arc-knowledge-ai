from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.exceptions import PipelineAbortedError

if TYPE_CHECKING:
    from app.pipeline.core.stage import BaseStage


class Phase(Enum):
    PRE_PIPELINE  = "pre_pipeline"
    PRE_STAGE     = "pre_stage"
    POST_STAGE    = "post_stage"
    POST_PIPELINE = "post_pipeline"
    ON_ERROR      = "on_error"


class HookResult(Enum):
    CONTINUE   = "continue"    # 继续执行
    SKIP_STAGE = "skip_stage"  # 跳过当前 Stage（幂等：已处理过）
    ABORT      = "abort"       # 终止整条 Pipeline（超配额等）


@dataclass
class HookEvent:
    phase: Phase
    ctx: ProcessingContext
    stage: "BaseStage | None" = None
    error: Exception | None = None


class BaseHook(ABC):
    """
    横切关注点的抽象基类。

    Hook 和 Stage 完全独立——Stage 不知道有 Hook 存在。
    Hook 通过 Strategy.hooks 声明后由 HookRunner 统一管理。
    """

    # 子类声明自己处理哪个（些）Phase
    phase: ClassVar[Phase | list[Phase]]

    # 数字越小越先执行
    # 建议：TenantGuard=10, QuotaGuard=20, IdempotencyGuard=30, Observability=100
    priority: ClassVar[int] = 100

    @abstractmethod
    async def handle(self, event: HookEvent) -> HookResult:
        ...


class HookRunner:
    """按优先级顺序执行所有 Hook"""

    def __init__(self, hooks: list[BaseHook]) -> None:
        self.hooks = sorted(hooks, key=lambda h: h.priority)

    async def fire(
        self,
        phase: Phase,
        ctx: ProcessingContext,
        stage: "BaseStage | None" = None,
        error: Exception | None = None,
    ) -> HookResult:
        event = HookEvent(phase=phase, ctx=ctx, stage=stage, error=error)
        for hook in self.hooks:
            hook_phases = hook.phase if isinstance(hook.phase, list) else [hook.phase]
            if phase not in hook_phases:
                continue
            result = await hook.handle(event)
            if result == HookResult.ABORT:
                raise PipelineAbortedError(
                    f"Pipeline aborted by {hook.__class__.__name__}"
                )
            if result == HookResult.SKIP_STAGE:
                return HookResult.SKIP_STAGE
        return HookResult.CONTINUE
