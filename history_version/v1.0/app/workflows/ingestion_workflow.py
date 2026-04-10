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


@workflow.defn(name="IngestionWorkflow")
class IngestionWorkflow:
    """
    文档入库工作流。

    三个 Activity 顺序执行，每个都有独立的重试策略。
    任意 Activity 失败时 Temporal 自动重试，重试从失败的 Activity 开始，
    不会重跑已完成的 Activity（Checkpoint 语义）。

    parse → chunk → embed_and_index
    """

    @workflow.run
    async def run(self, inp: IngestionInput) -> dict:
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
