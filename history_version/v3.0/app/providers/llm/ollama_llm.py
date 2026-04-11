from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.config.settings import settings
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, HealthStatus, LLMProvider


@registry.provider("ollama_llm")
class OllamaLLMProvider(LLMProvider):
    """
    Ollama 本地 LLM，通过 /api/chat 接口访问。
    支持流式和非流式生成，不依赖 OpenAI SDK。
    """

    provider_id = "ollama_llm"

    def __init__(self) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_llm_model

    async def health_check(self) -> HealthStatus:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._base_url}/api/tags")
                return HealthStatus.HEALTHY if r.status_code == 200 else HealthStatus.DEGRADED
        except Exception:
            return HealthStatus.UNHEALTHY

    async def generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model":    self._model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream":   False,
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

    async def stream_generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={
                    "model":    self._model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream":   True,
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
