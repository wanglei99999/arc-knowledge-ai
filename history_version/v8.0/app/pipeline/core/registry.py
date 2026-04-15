from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.pipeline.core.exceptions import (
    ProviderNotFoundError,
    StageNotFoundError,
    StrategyNotFoundError,
)

if TYPE_CHECKING:
    from app.pipeline.core.stage import BaseStage
    from app.pipeline.strategies.base_strategy import BaseStrategy
    from app.providers.base import BaseProvider


class ComponentRegistry:
    """
    全局单例注册中心，管理三张注册表：
    - stages:     名字 → BaseStage 类
    - providers:  provider_id → BaseProvider 类
    - strategies: strategy_id → BaseStrategy 类

    使用装饰器注册：
        @registry.stage("pdf_parser")
        class PDFParserStage(BaseStage): ...
    """

    _instance: "ComponentRegistry | None" = None

    def __new__(cls) -> "ComponentRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._stages: dict[str, type[Any]] = {}
            cls._instance._providers: dict[str, type[Any]] = {}
            cls._instance._strategies: dict[str, type[Any]] = {}
        return cls._instance

    # ── 注册装饰器 ───────────────────────────────────────────────────────────

    def stage(self, name: str):
        """装饰器：注册 Stage 实现类"""
        def decorator(cls: type) -> type:
            cls.name = name
            self._stages[name] = cls
            return cls
        return decorator

    def provider(self, provider_id: str):
        """装饰器：注册 Provider 实现类"""
        def decorator(cls: type) -> type:
            cls.provider_id = provider_id
            self._providers[provider_id] = cls
            return cls
        return decorator

    def strategy(self, strategy_id: str):
        """装饰器：注册 Strategy 实现类"""
        def decorator(cls: type) -> type:
            cls.strategy_id = strategy_id
            self._strategies[strategy_id] = cls
            return cls
        return decorator

    # ── 获取（按名字查找，实例化返回）────────────────────────────────────────

    def get_stage(self, name: str) -> "BaseStage":
        if name not in self._stages:
            raise StageNotFoundError(f"Stage '{name}' not registered. Available: {list(self._stages)}")
        return self._stages[name]()

    def get_provider(self, provider_id: str) -> "BaseProvider":
        if provider_id not in self._providers:
            raise ProviderNotFoundError(f"Provider '{provider_id}' not registered. Available: {list(self._providers)}")
        return self._providers[provider_id]()

    def get_strategy(self, strategy_id: str) -> "BaseStrategy":
        if strategy_id not in self._strategies:
            raise StrategyNotFoundError(f"Strategy '{strategy_id}' not registered. Available: {list(self._strategies)}")
        return self._strategies[strategy_id]()

    # ── 调试 ─────────────────────────────────────────────────────────────────

    def list_stages(self) -> list[str]:
        return list(self._stages)

    def list_providers(self) -> list[str]:
        return list(self._providers)

    def list_strategies(self) -> list[str]:
        return list(self._strategies)


# 全局单例——所有模块 import 这一个实例
registry = ComponentRegistry()
