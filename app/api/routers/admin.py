from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.config.settings import settings
from app.infrastructure.elasticsearch.client import reset_index
from app.infrastructure.milvus.client import reset_collection
from app.infrastructure.milvus.memory_collection import reset_memory_collection
from app.infrastructure.minio.client import empty_bucket
from app.infrastructure.postgres.client import get_session
from app.providers.llm.model_state import get_current_model, reset_model, set_current_model

router = APIRouter(prefix="/admin", tags=["admin"])

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

    ⚠️  仅在 app_env=development 时可用，生产环境返回 403。
    """
    if settings.app_env != "development":
        raise HTTPException(status_code=403, detail="Only available in development environment")

    async def _reset_pg() -> int:
        async with get_session() as session:
            # 按依赖顺序逐表 TRUNCATE，最终返回各表合计行数（截断前）
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


@router.post("/models/switch")
async def switch_ollama_model(model: str) -> dict:
    """
    热切换 Ollama 模型，无需重启服务。

    切换后新的 LLM 请求立即使用新模型（registry 每次返回新实例，读取最新状态）。

    Args:
        model: Ollama 模型名称，例如 "llama3.2" / "mistral" / "qwen2.5"
    """
    set_current_model(model)
    return {"provider": "ollama_llm", "model": model, "status": "switched"}


@router.delete("/models/switch")
async def reset_ollama_model() -> dict:
    """重置为 settings 默认模型。"""
    reset_model()
    return {"provider": "ollama_llm", "model": settings.ollama_llm_model, "status": "reset"}


@router.get("/models/current")
async def get_ollama_model() -> dict:
    """查询当前生效的 Ollama 模型。"""
    return {
        "provider": "ollama_llm",
        "model": get_current_model(settings.ollama_llm_model),
        "default": settings.ollama_llm_model,
    }
