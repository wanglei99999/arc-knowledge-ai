from __future__ import annotations

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import LLMProvider
from app.providers.llm.resilient_llm import ResilientLLMProvider


class ModelHub:
    """
    LLM Provider 路由入口。

    根据 ctx.config.llm_provider 选择主 Provider，
    并用 ResilientLLMProvider 包装以支持熔断降级。

    当主 Provider 和 fallback 相同时，直接返回主 Provider（避免无意义的包装）。
    """

    def get_provider(self, ctx: ProcessingContext) -> LLMProvider:
        primary_id = ctx.config.llm_provider
        fallback_id = settings.llm_fallback_provider

        # allowed_models 校验：非空列表 + 指定了模型时才拦截
        model = ctx.config.default_llm_model
        allowed = ctx.config.allowed_models
        if allowed and model and model not in allowed:
            raise PermissionError(
                f"Model {model!r} is not in the allowed list for tenant {ctx.tenant_id!r}"
            )

        primary: LLMProvider = registry.get_provider(primary_id)  # type: ignore[assignment]

        if primary_id == fallback_id:
            # 主备相同，不需要熔断包装
            return primary

        return ResilientLLMProvider(primary=primary, fallback_provider_id=fallback_id)


# 全局单例，RAGOrchestrator import 这一个
model_hub = ModelHub()
