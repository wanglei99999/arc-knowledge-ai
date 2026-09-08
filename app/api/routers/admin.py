from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.dependencies import UserContext, require_tenant_admin
from app.infrastructure.postgres.client import get_session
from app.services.model_config_service import ModelConfigService
from app.services.tenant_config_service import TenantConfigService
from app.services.usage_service import UsageService

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_tenant_admin)])


async def scoped_admin(tenant_id: str, user: UserContext = Depends(require_tenant_admin)):
    if tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return user


_tenant_config_svc = TenantConfigService()
_model_config_svc = ModelConfigService()
_usage_svc = UsageService()


@router.post("/dev/reset", summary="[DEV ONLY] 清空所有存储数据")
async def dev_reset() -> dict:
    """全局破坏性操作不属于租户管理员权限，保留拒绝响应兼容旧客户端。"""
    raise HTTPException(
        status_code=403,
        detail="Global reset is disabled in the multi-tenant API; use an operator-controlled offline procedure",
    )


# ── 租户 LLM 配置管理 ─────────────────────────────────────────────────────────


class TenantConfigBody(BaseModel):
    default_llm_provider: str = "openai_llm"
    default_llm_model: str = ""
    allowed_models: list[str] = []


@router.get("/tenants/{tenant_id}/config", summary="查询租户 LLM 配置")
async def get_tenant_config(tenant_id: str, user: UserContext = Depends(scoped_admin)) -> dict:
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
async def update_tenant_config(
    tenant_id: str, body: TenantConfigBody, user: UserContext = Depends(scoped_admin)
) -> dict:
    """
    新建或更新租户 LLM 配置（upsert）。

    - `default_llm_provider`：使用哪个引擎（openai_llm / ollama_llama）
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


# ── 模型单价配置 ───────────────────────────────────────────────────────────────


class ModelConfigBody(BaseModel):
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0
    context_window: int = 0


@router.get("/models", summary="查询所有模型单价配置")
async def list_model_configs() -> list[dict]:
    return await _model_config_svc.list_all()


@router.post("/models/{model_id}", summary="新增或更新模型单价配置")
async def upsert_model_config(model_id: str, body: ModelConfigBody) -> dict:
    raise HTTPException(403, detail="Global model pricing is operator-managed")


@router.delete("/models/{model_id}", summary="删除模型单价配置")
async def delete_model_config(model_id: str) -> dict:
    raise HTTPException(403, detail="Global model pricing is operator-managed")


# ── 用量查询 ──────────────────────────────────────────────────────────────────


@router.get("/tenants/{tenant_id}/usage", summary="查询租户 LLM 用量")
async def get_tenant_usage(
    tenant_id: str,
    start: date = Query(default=None),
    end: date = Query(default=None),
    group_by: str | None = Query(default=None),
    user: UserContext = Depends(scoped_admin),
) -> dict:
    today = date.today()
    start = start or today.replace(day=1)  # 默认本月第一天
    end = end or today
    return await _usage_svc.query_by_tenant(tenant_id, start, end, group_by=group_by)


# ── 系统聚合统计 ───────────────────────────────────────────────────────────────


@router.get("/stats", summary="系统聚合统计")
async def get_stats(user: UserContext = Depends(require_tenant_admin)) -> dict:
    async with get_session() as session:
        doc_row = await session.execute(
            text(
                "SELECT COUNT(*) AS cnt, COALESCE(SUM(file_size), 0) AS storage "
                "FROM documents WHERE deleted_at IS NULL AND tenant_id=:tenant_id"
            ),
            {"tenant_id": user.tenant_id},
        )
        chunk_row = await session.execute(
            text("SELECT COUNT(*) FROM document_chunks WHERE tenant_id=:tenant_id"),
            {"tenant_id": user.tenant_id},
        )
        session_row = await session.execute(
            text("SELECT COUNT(*) FROM sessions WHERE tenant_id=:tenant_id"),
            {"tenant_id": user.tenant_id},
        )
    doc = doc_row.mappings().one()
    return {
        "document_count": int(doc["cnt"]),
        "chunk_count": int(chunk_row.scalar_one()),
        "session_count": int(session_row.scalar_one()),
        "storage_bytes": int(doc["storage"]),
    }
