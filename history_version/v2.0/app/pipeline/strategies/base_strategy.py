from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.pipeline.core.context import TenantConfig
from app.pipeline.core.hook import BaseHook
from app.pipeline.core.pipeline import Pipeline


class BaseStrategy(ABC):
    """
    处理方案抽象基类。

    Strategy 回答：对于这种情况，用什么 Pipeline 配置？
    Strategy 是 Pipeline 的工厂，同时声明本策略需要哪些 Hook。

    不同租户可以绑定不同 Strategy：
        tenant_a.ingestion_strategy = "standard"
        tenant_b.ingestion_strategy = "premium"
    """

    strategy_id: ClassVar[str]

    # 子类声明本策略需要挂载哪些 Hook 类
    # Phase 0 先用空列表，Phase 3 再逐步开启
    hooks: ClassVar[list[type[BaseHook]]] = []

    @abstractmethod
    def build_pipeline(self, doc_type: str, config: TenantConfig) -> Pipeline:
        """根据文档类型和租户配置，组装并返回 Pipeline"""
        ...

    def get_hooks(self) -> list[BaseHook]:
        """实例化本策略声明的所有 Hook"""
        return [hook_cls() for hook_cls in self.hooks]

    def build_pipeline_with_hooks(self, doc_type: str, config: TenantConfig) -> Pipeline:
        """构建带 Hook 的完整 Pipeline（便捷方法）"""
        return self.build_pipeline(doc_type, config).with_hooks(self.get_hooks())

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.strategy_id!r})"
