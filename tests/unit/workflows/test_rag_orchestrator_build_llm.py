"""build_llm 是 LLM 选择的唯一入口：主模型必须来自租户配置并经 ModelHub，
而不是 fallback 备胎或直连 registry。回归 CRITICAL-2。"""

from __future__ import annotations

from app.pipeline.core.context import TenantConfig
from app.providers.llm.model_hub import model_hub
from app.workflows.rag_orchestrator import RAGOrchestrator


async def test_build_llm_selects_provider_from_tenant_config(monkeypatch):
    orch = RAGOrchestrator()

    async def fake_get_or_default(tenant_id: str) -> TenantConfig:
        # 租户配置的主 provider 是 openai_llm（不是 fallback 备胎 ollama_llm）
        return TenantConfig(tenant_id=tenant_id, llm_provider="openai_llm")

    monkeypatch.setattr(orch._tenant_config_svc, "get_or_default", fake_get_or_default)

    captured: dict = {}
    sentinel = object()

    def fake_get_provider(ctx):
        captured["ctx"] = ctx
        return sentinel

    monkeypatch.setattr(model_hub, "get_provider", fake_get_provider)

    ctx, provider = await orch.build_llm("tenant-1", model="gpt-4o-mini")

    # provider 经 ModelHub 取得（带熔断/校验），不是直连 registry
    assert provider is sentinel
    # 主模型来自租户配置，不是 settings.llm_fallback_provider
    assert captured["ctx"].config.llm_provider == "openai_llm"
    # 请求级 model 覆盖写入 default_llm_model
    assert captured["ctx"].config.default_llm_model == "gpt-4o-mini"
    assert ctx.tenant_id == "tenant-1"


async def test_build_llm_without_model_keeps_config_default(monkeypatch):
    orch = RAGOrchestrator()

    async def fake_get_or_default(tenant_id: str) -> TenantConfig:
        return TenantConfig(tenant_id=tenant_id, llm_provider="ollama_llm")

    monkeypatch.setattr(orch._tenant_config_svc, "get_or_default", fake_get_or_default)
    monkeypatch.setattr(model_hub, "get_provider", lambda ctx: ctx)

    ctx, _ = await orch.build_llm("tenant-2")

    # 不传 model 时保留租户配置原值（default_llm_model 默认空串）
    assert ctx.config.llm_provider == "ollama_llm"
    assert ctx.config.default_llm_model == ""
