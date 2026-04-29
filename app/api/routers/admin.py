from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.config.settings import settings
from app.infrastructure.elasticsearch.client import reset_index
from app.infrastructure.milvus.client import reset_collection
from app.infrastructure.milvus.memory_collection import reset_memory_collection
from app.infrastructure.minio.client import empty_bucket
from app.infrastructure.postgres.client import get_session
from app.services.tenant_config_service import TenantConfigService

router = APIRouter(prefix="/admin", tags=["admin"])

_tenant_config_svc = TenantConfigService()

_PG_TABLES = (
    "message_citations",
    "memories",
    "messages",
    "sessions",
    "document_chunks",
    "documents",
    "spaces",
)


@router.post("/dev/reset", summary="[DEV ONLY] 清空所有存储数据")
async def dev_reset() -> dict:
    """
    一键清空所有存储（PostgreSQL / Milvus / Elasticsearch / MinIO）。

    仅在 app_env=development 时可用，生产环境返回 403。
    """
    if settings.app_env != "development":
        raise HTTPException(status_code=403, detail="Only available in development environment")

    async def _reset_pg() -> int:
        async with get_session() as session:
            total = 0
            for table in _PG_TABLES:
                result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                total += result.scalar_one()
            await session.execute(
                text(f"TRUNCATE {', '.join(_PG_TABLES)} RESTART IDENTITY CASCADE")
            )
            return total

    pg_count, (milvus_chunks, milvus_memories), es_count, minio_count = await asyncio.gather(
        _reset_pg(),
        asyncio.gather(reset_collection(), reset_memory_collection()),
        reset_index(),
        empty_bucket(),
    )

    return {
        "status": "ok",
        "cleared": {
            "postgres_rows": pg_count,
            "milvus_chunk_vectors": milvus_chunks,
            "milvus_memory_vectors": milvus_memories,
            "elasticsearch_docs": es_count,
            "minio_objects": minio_count,
        },
    }


# ── 租户 LLM 配置管理 ─────────────────────────────────────────────────────────

class TenantConfigBody(BaseModel):
    default_llm_provider: str = "openai_llm"
    default_llm_model: str = ""
    allowed_models: list[str] = []


@router.get("/tenants/{tenant_id}/config", summary="查询租户 LLM 配置")
async def get_tenant_config(tenant_id: str) -> dict:
    """
    查询指定租户的 LLM 配置。DB 中无记录时返回系统默认值。
    """
    config = await _tenant_config_svc.get_or_default(tenant_id)
    return {
        "tenant_id": tenant_id,
        "default_llm_provider": config.llm_provider,
        "default_llm_model": config.default_llm_model,
        "allowed_models": config.allowed_models,
    }


@router.put("/tenants/{tenant_id}/config", summary="新建或更新租户 LLM 配置")
async def update_tenant_config(tenant_id: str, body: TenantConfigBody) -> dict:
    """
    新建或更新租户 LLM 配置（upsert）。

    - `default_llm_provider`：使用哪个引擎（openai_llm / ollama_llm）
    - `default_llm_model`：具体模型名，空字符串表示使用引擎自己的 settings 默认值
    - `allowed_models`：允许使用的模型白名单，空列表表示不限制
    """
    row = await _tenant_config_svc.update(
        tenant_id=tenant_id,
        default_llm_provider=body.default_llm_provider,
        default_llm_model=body.default_llm_model,
        allowed_models=body.allowed_models,
    )
    return {"status": "ok", "tenant_id": tenant_id, "config": row}
