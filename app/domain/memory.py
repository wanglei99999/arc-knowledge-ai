from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class MemoryCategory(Enum):
    FACT       = "fact"        # 客观事实：「用户是 CFO」
    PREFERENCE = "preference"  # 偏好：「用户喜欢简洁回答」
    GOAL       = "goal"        # 目标：「用户在审查并购合同」


@dataclass
class Session:
    """对话会话（Layer 1 / Layer 2 载体）"""
    session_id: str
    tenant_id: str
    user_id: str
    space_id: str | None = None      # 会话归属空间，创建后不可变
    title: str | None = None
    summary: str | None = None       # Layer 2 情节记忆：中间段压缩摘要
    message_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Message:
    """单条对话消息"""
    message_id: str
    session_id: str
    tenant_id: str
    user_id: str
    role: str        # "user" | "assistant"
    content: str
    token_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Memory:
    """语义记忆条目（Layer 3）"""
    memory_id: str
    tenant_id: str
    user_id: str
    category: MemoryCategory
    content: str                        # 提取的事实 / 偏好 / 目标文本
    source_session_id: str | None = None
    confidence: float = 1.0             # [0, 1]
    embedding: list[float] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class WorkingMemory:
    """工作记忆：一次请求所需的三层组件"""
    anchor: list[Message]     # First-2 锚点消息（可为空，表示消息总数 <= last_n）
    summary: str | None       # 情节记忆摘要
    recent: list[Message]     # Last-N 近期消息
    message_count: int        # session 总消息数
