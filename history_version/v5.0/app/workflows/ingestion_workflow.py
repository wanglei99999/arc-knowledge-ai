from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.ingestion_activities import (
        IngestionInput,
        chunk_activity,
        embed_and_index_activity,
        parse_activity,
    )

_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
)

# Workflow 版本常量
# 每次修改 Workflow 执行逻辑时递增，用于 workflow.patched() 安全滚动升级
_V1 = 1  # Phase 4：当前版本，三 Activity 顺序链路


@workflow.defn(name="IngestionWorkflow")
class IngestionWorkflow:
    """
    文档入库工作流。

    三个 Activity 顺序执行，每个都有独立的重试策略。
    任意 Activity 失败时 Temporal 自动重试，重试从失败的 Activity 开始，
    不会重跑已完成的 Activity（Checkpoint 语义）。

    parse → chunk → embed_and_index

    版本化说明（workflow.patched）：
    当需要修改 Workflow 执行逻辑时，使用 workflow.patched("patch-id") 区分新旧代码路径，
    确保已在执行中的旧 Workflow 不受影响。新 Workflow 走新路径，旧 Workflow 走旧路径。

    示例（未来某次变更时的写法）：
        if workflow.patched("add-validation-activity"):
            await workflow.execute_activity(validate_activity, ...)
        # else: 旧 Workflow 跳过该步骤
    """

    @workflow.run
    async def run(self, inp: IngestionInput) -> dict:
        # 版本标记：标识当前 Workflow 逻辑版本（Phase 4）
        # 未来修改 Workflow 逻辑时，在此处新增 patched() 分支
        workflow.patched("phase4-three-stage-pipeline")

        # Activity 1: 解析
        parsed_dict = await workflow.execute_activity(
            parse_activity,
            inp,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=_RETRY,
        )

        # Activity 2: 切片
        chunk_dicts = await workflow.execute_activity(
            chunk_activity,
            args=[inp, parsed_dict],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )

        # Activity 3: 向量化 + 写库
        indexed_count = await workflow.execute_activity(
            embed_and_index_activity,
            args=[inp, chunk_dicts],
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=_RETRY,
        )

        return {
            "document_id": inp.document_id,
            "indexed_chunks": indexed_count,
            "status": "indexed",
        }
