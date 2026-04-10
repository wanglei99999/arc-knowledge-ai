from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, ClassVar

from app.pipeline.core.context import ProcessingContext


class HealthStatus(Enum):
    HEALTHY   = "healthy"
    DEGRADED  = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ParsedDocument:
    """解析后的文档结构"""
    text: str
    title: str | None = None
    metadata: dict | None = None
    page_count: int | None = None


@dataclass
class ChatMessage:
    role: str   # "system" | "user" | "assistant"
    content: str


# ── 基类 ─────────────────────────────────────────────────────────────────────

class BaseProvider(ABC):
    provider_id: ClassVar[str]

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.provider_id!r})"


# ── Embedding ─────────────────────────────────────────────────────────────────

class EmbeddingProvider(BaseProvider):
    @abstractmethod
    async def embed(
        self,
        ctx: ProcessingContext,
        texts: list[str],
    ) -> list[list[float]]:
        """批量文本转向量"""
        ...

    @abstractmethod
    def get_dimension(self) -> int:
        """向量维度，用于 Milvus Collection 初始化"""
        ...

    @abstractmethod
    def get_model_name(self) -> str:
        ...


# ── LLM ──────────────────────────────────────────────────────────────────────

class LLMProvider(BaseProvider):
    @abstractmethod
    async def generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> str:
        """非流式生成"""
        ...

    @abstractmethod
    async def stream_generate(
        self,
        ctx: ProcessingContext,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[str]:
        """流式生成，yield token"""
        ...


# ── Parser ────────────────────────────────────────────────────────────────────

class ParserProvider(BaseProvider):
    @abstractmethod
    async def parse(
        self,
        ctx: ProcessingContext,
        file_path: str,
    ) -> ParsedDocument:
        """解析文件，返回结构化文本"""
        ...

    @abstractmethod
    def supports(self, mime_type: str) -> bool:
        """声明支持的文件类型"""
        ...


# ── Rerank ────────────────────────────────────────────────────────────────────

class RerankProvider(BaseProvider):
    @abstractmethod
    async def rerank(
        self,
        ctx: ProcessingContext,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        """返回 [(原始索引, score)] 按相关性降序"""
        ...
