from __future__ import annotations

import asyncio
from datetime import timedelta
from functools import partial
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import text
from temporalio.client import Client

from app.config.settings import settings
from app.infrastructure.elasticsearch.client import _get_client as get_es_client
from app.infrastructure.milvus.client import _get_client as get_milvus_client
from app.infrastructure.minio.client import _make_client as get_minio_client
from app.infrastructure.postgres.client import get_session
from app.infrastructure.redis.client import get_redis
from app.services.readiness_service import Check, ReadinessService


async def check_postgres() -> None:
    async with get_session() as session:
        await session.execute(text("SELECT 1"))


async def check_redis() -> None:
    if not await get_redis().ping():
        raise ConnectionError("Redis PING returned false")


async def check_minio() -> None:
    client = get_minio_client()
    await asyncio.to_thread(client.head_bucket, Bucket=settings.minio_bucket)


async def check_milvus() -> None:
    client = get_milvus_client()
    collections = await asyncio.to_thread(client.list_collections)
    if "arc_chunk_embeddings" in collections:
        await asyncio.to_thread(client.get_collection_stats, "arc_chunk_embeddings")


async def check_elasticsearch() -> None:
    client = get_es_client()
    if not await client.ping():
        raise ConnectionError("Elasticsearch ping returned false")
    if not await client.indices.exists(index="arc_chunks"):
        raise ConnectionError("Elasticsearch index arc_chunks is missing")
    await client.indices.stats(index="arc_chunks")


async def check_temporal() -> None:
    client = await Client.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )
    healthy = await client.service_client.check_health(timeout=timedelta(seconds=2))
    if not healthy:
        raise ConnectionError("Temporal health check returned false")


async def check_http_endpoint(url: str, api_key: str = "") -> None:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=settings.runtime_probe_timeout_seconds) as client:
        response = await client.get(url.rstrip("/"), headers=headers)
    if response.status_code >= 500:
        raise ConnectionError(f"endpoint returned HTTP {response.status_code}")


def enabled_optional_services() -> set[str]:
    return {
        item.strip().lower()
        for item in settings.runtime_optional_services.split(",")
        if item.strip()
    }


def _phoenix_http_url() -> str:
    endpoint = urlsplit(settings.otlp_endpoint)
    hostname = endpoint.hostname or "localhost"
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit((endpoint.scheme or "http", f"{host}:6006", "", "", ""))


def build_readiness_service() -> ReadinessService:
    required: dict[str, Check] = {
        "postgres": check_postgres,
        "redis": check_redis,
        "minio": check_minio,
        "milvus": check_milvus,
        "elasticsearch": check_elasticsearch,
        "temporal": check_temporal,
    }

    embedding_url = settings.openai_embedding_base_url or settings.openai_base_url
    embedding_key = settings.openai_embedding_api_key or settings.openai_api_key
    optional: dict[str, Check] = {
        "llm": partial(
            check_http_endpoint,
            settings.openai_base_url,
            settings.openai_api_key,
        ),
        "embedding": partial(check_http_endpoint, embedding_url, embedding_key),
    }

    enabled = enabled_optional_services()
    if "rerank" in enabled:
        optional["infinity"] = partial(check_http_endpoint, settings.infinity_base_url)
    if "ocr" in enabled:
        optional["paddleocr"] = partial(check_http_endpoint, settings.ocr_service_url)
        optional["mineru"] = partial(check_http_endpoint, settings.mineru_service_url)
    if "observe" in enabled:
        optional["phoenix"] = partial(check_http_endpoint, _phoenix_http_url())

    return ReadinessService(
        required=required,
        optional=optional,
        timeout_seconds=settings.runtime_probe_timeout_seconds,
    )
