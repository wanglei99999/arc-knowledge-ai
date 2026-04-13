from __future__ import annotations

"""
Ollama 模型热切换状态。

模块级变量保存运行时覆盖的模型名称，供 OllamaLLMProvider 读取。
registry.get_provider() 每次返回新实例，因此每次调用都会读取最新值。
"""

_current_model: str | None = None


def get_current_model(default: str) -> str:
    """返回当前生效的模型名，未设置时返回 default（来自 settings）。"""
    return _current_model or default


def set_current_model(model: str) -> None:
    """运行时切换 Ollama 模型，无需重启服务。"""
    global _current_model
    _current_model = model


def reset_model() -> None:
    """重置为 settings 默认值。"""
    global _current_model
    _current_model = None
