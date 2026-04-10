from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from app.config.settings import settings
from app.workflows.ingestion_activities import (
    chunk_activity,
    embed_and_index_activity,
    parse_activity,
)
from app.workflows.ingestion_workflow import IngestionWorkflow


async def run_worker() -> None:
    """启动 Temporal Worker，监听 ingestion task queue"""

    # 触发所有 Stage/Provider/Strategy 注册
    import app.pipeline.stages.chunking.token_chunker  # noqa: F401
    import app.pipeline.stages.embedding.embed_stage  # noqa: F401
    import app.pipeline.stages.parsing.parser_stage  # noqa: F401
    import app.pipeline.strategies.ingestion.standard_strategy  # noqa: F401
    import app.providers.embedding.openai_embedding  # noqa: F401
    import app.providers.parser.unstructured_provider  # noqa: F401

    client = await Client.connect(settings.temporal_host)

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[IngestionWorkflow],
        activities=[parse_activity, chunk_activity, embed_and_index_activity],
    )

    print(f"Worker started: task_queue={settings.temporal_task_queue}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
