from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.exceptions import PreconditionError

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


class BaseStage(ABC, Generic[TInput, TOutput]):
    """
    流水线中的最小处理单元。

    设计要点：
    - 每个 Stage 声明 requires（前置条件）和 produces（后置保证）
    - _execute() 是唯一需要子类实现的方法
    - execute() 由框架调用，负责前置检查和 Hook 注入点
    """

    name: ClassVar[str]          # 子类必须声明，用于注册和日志
    version: ClassVar[str] = "1.0"

    # 前置条件：context.metadata 里必须有这些 key，才能执行本 Stage
    requires: ClassVar[frozenset[str]] = frozenset()

    # 后置保证：本 Stage 执行后会往 context.metadata 写这些 key
    produces: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    async def _execute(self, ctx: ProcessingContext, input: TInput) -> TOutput:
        """业务逻辑在这里实现，不要覆写 execute()"""
        ...

    async def execute(self, ctx: ProcessingContext, input: TInput) -> TOutput:
        """
        框架入口，子类不要覆写此方法。
        前置检查由框架做，业务代码只关心 _execute()。
        """
        self._check_preconditions(ctx)
        return await self._execute(ctx, input)

    def _check_preconditions(self, ctx: ProcessingContext) -> None:
        missing = self.requires - ctx.metadata.keys()
        if missing:
            raise PreconditionError(
                f"Stage '{self.name}' requires context keys: {missing}"
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, v={self.version})"
