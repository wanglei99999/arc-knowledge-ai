from __future__ import annotations

from typing import Generic, TypeVar

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.hook import BaseHook, HookResult, HookRunner, Phase
from app.pipeline.core.stage import BaseStage

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


class Pipeline(Generic[TInput, TOutput]):
    """
    有序 Stage 链。

    设计要点：
    - Pipeline 本身不含业务逻辑，只做编排
    - Pipeline 可以被另一个 Pipeline 当作 Stage 使用（as_stage）
    - then() 返回新 Pipeline，原 Pipeline 不变（不可变构造）
    """

    def __init__(
        self,
        stages: list[BaseStage],
        hooks: list[BaseHook] | None = None,
    ) -> None:
        self.stages = stages
        self._hook_runner = HookRunner(hooks or [])

    async def run(self, ctx: ProcessingContext, input: TInput) -> TOutput:
        await self._hook_runner.fire(Phase.PRE_PIPELINE, ctx)

        current = input
        for stage in self.stages:
            result = await self._hook_runner.fire(Phase.PRE_STAGE, ctx, stage=stage)

            if result == HookResult.SKIP_STAGE:
                # 幂等：该 Stage 已执行过，跳过
                continue

            try:
                current = await stage.execute(ctx, current)
                await self._hook_runner.fire(Phase.POST_STAGE, ctx, stage=stage)
            except Exception as e:
                await self._hook_runner.fire(Phase.ON_ERROR, ctx, stage=stage, error=e)
                raise

        await self._hook_runner.fire(Phase.POST_PIPELINE, ctx)
        return current  # type: ignore[return-value]

    # ── Builder API ─────────────────────────────────────────────────────────

    @classmethod
    def start(cls, stage: BaseStage) -> "Pipeline":
        """链式构造入口：Pipeline.start(stage).then(...)"""
        return cls(stages=[stage])

    def then(self, stage: BaseStage) -> "Pipeline":
        """追加一个 Stage，返回新 Pipeline"""
        return Pipeline(stages=[*self.stages, stage], hooks=self._hook_runner.hooks)

    def with_hooks(self, hooks: list[BaseHook]) -> "Pipeline":
        """附加 Hook 列表，返回新 Pipeline"""
        return Pipeline(stages=self.stages, hooks=hooks)

    def as_stage(self, name: str) -> "_PipelineStage":
        """把整条 Pipeline 包装成一个 Stage，用于 SubPipeline 组合"""
        return _PipelineStage(name=name, pipeline=self)

    def __repr__(self) -> str:
        names = " → ".join(s.name for s in self.stages)
        return f"Pipeline([{names}])"


class _PipelineStage(BaseStage[TInput, TOutput]):
    """将 Pipeline 包装为 Stage，实现 SubPipeline 组合"""

    def __init__(self, name: str, pipeline: Pipeline) -> None:
        self._name = name
        self._pipeline = pipeline

    @property  # type: ignore[override]
    def name(self) -> str:  # type: ignore[override]
        return self._name

    async def _execute(self, ctx: ProcessingContext, input: TInput) -> TOutput:
        return await self._pipeline.run(ctx, input)
