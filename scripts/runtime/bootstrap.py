from __future__ import annotations

import asyncio

from app.config.settings import settings
from app.infrastructure.elasticsearch.client import (
    _ensure_index,
)
from app.infrastructure.elasticsearch.client import (
    _get_client as get_es_client,
)
from app.infrastructure.minio.client import (
    _ensure_bucket,
)
from app.infrastructure.minio.client import (
    _make_client as get_minio_client,
)
from scripts.migrate import migrate


async def bootstrap() -> None:
    await migrate()

    minio = get_minio_client()
    await asyncio.to_thread(_ensure_bucket, minio, settings.minio_bucket)

    elasticsearch = get_es_client()
    await asyncio.to_thread(_ensure_index, elasticsearch)

    print("Runtime storage initialization completed successfully.")


if __name__ == "__main__":
    asyncio.run(bootstrap())
