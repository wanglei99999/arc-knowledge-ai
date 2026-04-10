from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine


@dataclass
class DomainEvent:
    """领域事件基类"""
    type: str
    tenant_id: str
    document_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# 预定义事件类型常量
class EventType:
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED = "stage.failed"
    DOCUMENT_INDEXED = "document.indexed"
    DOCUMENT_FAILED = "document.failed"
    TOKEN_CONSUMED = "token.consumed"


Handler = Callable[[DomainEvent], Coroutine[Any, Any, None]]


class EventBus:
    """
    进程内异步事件总线（不走网络）。
    组件间解耦通信：Stage 发事件，监听者异步响应。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str) -> Callable[[Handler], Handler]:
        """装饰器：订阅某类事件"""
        def decorator(fn: Handler) -> Handler:
            self._handlers[event_type].append(fn)
            return fn
        return decorator

    async def publish(self, event: DomainEvent) -> None:
        """发布事件，按顺序调用所有订阅者"""
        for handler in self._handlers.get(event.type, []):
            await handler(event)

    async def publish_all(self, events: list[DomainEvent]) -> None:
        for event in events:
            await self.publish(event)


# 全局单例
event_bus = EventBus()
