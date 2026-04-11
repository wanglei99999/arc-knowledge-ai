"""Pipeline 框架单元测试——不依赖任何外部服务"""
import pytest

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.exceptions import PipelineAbortedError, PreconditionError
from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase
from app.pipeline.core.pipeline import Pipeline
from app.pipeline.core.stage import BaseStage


# ── 测试用 Stage ──────────────────────────────────────────────────────────────

class UpperStage(BaseStage[str, str]):
    name = "upper"
    async def _execute(self, ctx: ProcessingContext, input: str) -> str:
        return input.upper()


class ReverseStage(BaseStage[str, str]):
    name = "reverse"
    async def _execute(self, ctx: ProcessingContext, input: str) -> str:
        return input[::-1]


class RequiresKeyStage(BaseStage[str, str]):
    name = "requires_key"
    requires = frozenset({"special_key"})
    async def _execute(self, ctx: ProcessingContext, input: str) -> str:
        return input + ctx.metadata["special_key"]


# ── 测试用 Hook ───────────────────────────────────────────────────────────────

class RecordingHook(BaseHook):
    phase = [Phase.PRE_STAGE, Phase.POST_STAGE]
    priority = 10

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def handle(self, event: HookEvent) -> HookResult:
        stage_name = event.stage.name if event.stage else "none"
        self.calls.append(f"{event.phase.value}:{stage_name}")
        return HookResult.CONTINUE


class AbortHook(BaseHook):
    phase = Phase.PRE_STAGE
    priority = 1

    async def handle(self, event: HookEvent) -> HookResult:
        return HookResult.ABORT


class SkipHook(BaseHook):
    phase = Phase.PRE_STAGE
    priority = 1

    async def handle(self, event: HookEvent) -> HookResult:
        return HookResult.SKIP_STAGE


# ── 测试 ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_runs_stages_in_order(fake_ctx: ProcessingContext) -> None:
    pipeline = Pipeline.start(UpperStage()).then(ReverseStage())
    result = await pipeline.run(fake_ctx, "hello")
    assert result == "OLLEH"


@pytest.mark.asyncio
async def test_pipeline_single_stage(fake_ctx: ProcessingContext) -> None:
    pipeline = Pipeline.start(UpperStage())
    assert await pipeline.run(fake_ctx, "world") == "WORLD"


@pytest.mark.asyncio
async def test_then_is_immutable(fake_ctx: ProcessingContext) -> None:
    p1 = Pipeline.start(UpperStage())
    p2 = p1.then(ReverseStage())
    assert len(p1.stages) == 1
    assert len(p2.stages) == 2
    # p1 还能正常跑
    assert await p1.run(fake_ctx, "hi") == "HI"


@pytest.mark.asyncio
async def test_precondition_error_raised(fake_ctx: ProcessingContext) -> None:
    pipeline = Pipeline.start(RequiresKeyStage())
    with pytest.raises(PreconditionError, match="special_key"):
        await pipeline.run(fake_ctx, "input")


@pytest.mark.asyncio
async def test_precondition_passes_with_metadata(fake_ctx: ProcessingContext) -> None:
    ctx = fake_ctx.with_metadata(special_key="!")
    pipeline = Pipeline.start(RequiresKeyStage())
    result = await pipeline.run(ctx, "hi")
    assert result == "hi!"


@pytest.mark.asyncio
async def test_hooks_fire_in_order(fake_ctx: ProcessingContext) -> None:
    hook = RecordingHook()
    pipeline = Pipeline.start(UpperStage()).then(ReverseStage()).with_hooks([hook])
    await pipeline.run(fake_ctx, "x")
    assert hook.calls == [
        "pre_stage:upper",
        "post_stage:upper",
        "pre_stage:reverse",
        "post_stage:reverse",
    ]


@pytest.mark.asyncio
async def test_abort_hook_raises(fake_ctx: ProcessingContext) -> None:
    pipeline = Pipeline.start(UpperStage()).with_hooks([AbortHook()])
    with pytest.raises(PipelineAbortedError):
        await pipeline.run(fake_ctx, "x")


@pytest.mark.asyncio
async def test_skip_hook_skips_stage(fake_ctx: ProcessingContext) -> None:
    # SkipStage → 每个 Stage 都被跳过，输出维持输入不变
    pipeline = Pipeline.start(UpperStage()).with_hooks([SkipHook()])
    result = await pipeline.run(fake_ctx, "hello")
    assert result == "hello"   # 没有经过 UpperStage


@pytest.mark.asyncio
async def test_pipeline_as_stage(fake_ctx: ProcessingContext) -> None:
    sub = Pipeline.start(UpperStage()).as_stage("sub_pipeline")
    outer = Pipeline.start(sub).then(ReverseStage())
    assert await outer.run(fake_ctx, "abc") == "CBA"
