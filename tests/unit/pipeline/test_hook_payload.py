from __future__ import annotations

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.hook import BaseHook, HookEvent, HookResult, Phase
from app.pipeline.core.pipeline import Pipeline
from app.pipeline.core.stage import BaseStage


class _Doubler(BaseStage[int, int]):
    name = "doubler"

    async def _execute(self, ctx: ProcessingContext, input: int) -> int:
        return input * 2


class _RecordingHook(BaseHook):
    phase = Phase.POST_STAGE

    def __init__(self) -> None:
        self.payloads: list[object] = []

    async def handle(self, event: HookEvent) -> HookResult:
        self.payloads.append(event.payload)
        return HookResult.CONTINUE


async def test_post_stage_hook_receives_stage_output(fake_ctx):
    hook = _RecordingHook()
    pipeline = Pipeline(stages=[_Doubler()], hooks=[hook])
    result = await pipeline.run(fake_ctx, 21)
    assert result == 42
    assert hook.payloads == [42]  # Hook 看到的就是 Stage 的输出
