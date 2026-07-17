from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from app.config.settings import settings
from app.infrastructure.telemetry.otel import setup_tracing
from app.workflows.ingestion_activities import (
    chunk_activity,
    embed_and_index_activity,
    parse_activity,
)
from app.workflows.ingestion_workflow import IngestionWorkflow


def init_worker_tracing() -> None:
    """worker 进程自己的 TracerProvider（修复缺陷2：此前 worker 里 span 全是 no-op）。"""
    if settings.otel_enabled:
        setup_tracing(f"{settings.otel_service_name}-worker", settings.otlp_endpoint)


async def run_worker() -> None:
    """启动 Temporal Worker，监听 ingestion task queue"""
    init_worker_tracing()

    # 触发所有 Stage/Provider/Strategy 注册
    import app.pipeline.stages.chunking.token_chunker  # noqa: F401
    import app.pipeline.stages.embedding.embed_stage  # noqa: F401
    import app.pipeline.stages.embedding.es_index_stage  # noqa: F401
    import app.pipeline.stages.embedding.milvus_index_stage  # noqa: F401
    import app.pipeline.stages.parsing.parser_stage  # noqa: F401
    import app.pipeline.strategies.ingestion.ocr_strategy  # noqa: F401
    import app.pipeline.strategies.ingestion.standard_strategy  # noqa: F401
    import app.providers.embedding.openai_embedding  # noqa: F401
    import app.providers.parser.mineru_provider  # noqa: F401
    import app.providers.parser.paddleocr_provider  # noqa: F401
    import app.providers.parser.smart_parser_provider  # noqa: F401
    import app.providers.parser.unstructured_provider  # noqa: F401

    interceptors = [TracingInterceptor()] if settings.otel_enabled else []
    client = await Client.connect(settings.temporal_host, interceptors=interceptors)

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
