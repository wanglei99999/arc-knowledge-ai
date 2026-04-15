from __future__ import annotations

from fastapi import APIRouter

from app.providers.llm.model_state import get_current_model, reset_model, set_current_model
from app.config.settings import settings

router = APIRouter(prefix="/admin", tags=["admin"])


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
