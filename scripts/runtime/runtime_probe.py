from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from sqlalchemy import text
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

from app.config.settings import settings
from app.infrastructure.postgres.client import get_session

REQUIRED_TABLES = {
    "users",
    "spaces",
    "documents",
    "document_chunks",
    "sessions",
    "messages",
    "message_attachments",
    "message_citations",
    "memories",
    "tenant_configs",
    "model_configs",
    "usage_records",
}


def worker_state(pollers: Sequence[object]) -> tuple[bool, str]:
    count = len(pollers)
    if count == 0:
        return False, "no workflow poller"
    suffix = "" if count == 1 else "s"
    return True, f"{count} workflow poller{suffix}"


def missing_schema_tables(
    existing: set[str],
    required: set[str] = REQUIRED_TABLES,
) -> list[str]:
    return sorted(required - existing)


async def probe_schema() -> dict[str, object]:
    async with get_session() as session:
        result = await session.execute(
            text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
        )

    missing = missing_schema_tables(set(result.scalars()))
    detail = (
        "all migration tables present" if not missing else f"missing tables: {', '.join(missing)}"
    )
    return {"name": "schema", "ok": not missing, "detail": detail}


async def probe_worker() -> dict[str, object]:
    client = await Client.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )
    response = await client.workflow_service.describe_task_queue(
        DescribeTaskQueueRequest(
            namespace=settings.temporal_namespace,
            task_queue=TaskQueue(name=settings.temporal_task_queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        )
    )
    ok, detail = worker_state(list(response.pollers))
    return {
        "name": "worker",
        "ok": ok,
        "detail": detail,
        "task_queue": settings.temporal_task_queue,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe the Incipit local runtime")
    parser.add_argument("probe", choices=["worker", "schema"])
    parser.add_argument("--json", action="store_true")
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = await (probe_worker() if args.probe == "worker" else probe_schema())
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        result = {"name": args.probe, "ok": False, "detail": detail}
        print(json.dumps(result) if args.json else detail)
        return 2

    print(json.dumps(result) if args.json else result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
