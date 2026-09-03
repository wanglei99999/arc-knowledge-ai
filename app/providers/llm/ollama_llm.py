from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.events import DomainEvent, EventType, event_bus
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, HealthStatus, LLMProvider

logger = logging.getLogger(__name__)


@registry.provider("ollama_llm")
class OllamaLLMProvider(LLMProvider):
    """
    Ollama 本地 LLM，通过 /api/chat 接口访问。
    支持流式和非流式生成，不依赖 OpenAI SDK。
    """

    provider_id = "ollama_llm"

    def __init__(self) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._default_model = settings.ollama_llm_model

    def _resolve_model(self, ctx: ProcessingContext) -> str:
        """三层优先级：ctx.config.default_llm_model > settings.ollama_llm_model"""
        return ctx.config.default_llm_model or self._default_model

    async def health_check(self) -> HealthStatus:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._base_url}/api/tags")
                return HealthStatus.HEALTHY if r.status_code == 200 else HealthStatus.DEGRADED
        except Exception:
            return HealthStatus.UNHEALTHY

    def get_context_window(self) -> int:
        return settings.ollama_context_window

    async def _publish_usage(
        self,
        ctx: ProcessingContext,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        try:
            await event_bus.publish(
                DomainEvent(
                    type=EventType.TOKEN_CONSUMED,
                    tenant_id=ctx.tenant_id,
                    payload={
                        "model": model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                )
            )
        except Exception as e:
            logger.warning("Failed to publish TOKEN_CONSUMED event: %s", e)

    async def generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> str:
        model = self._resolve_model(ctx)
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            await self._publish_usage(
                ctx,
                model,
                data.get("prompt_eval_count", 0),
                data.get("eval_count", 0),
            )
            return data["message"]["content"]

    async def stream_generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[str]:
        model = self._resolve_model(ctx)
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    # 最后一个 chunk done=true，包含 token 统计
                    if data.get("done"):
                        await self._publish_usage(
                            ctx,
                            model,
                            data.get("prompt_eval_count", 0),
                            data.get("eval_count", 0),
                        )
