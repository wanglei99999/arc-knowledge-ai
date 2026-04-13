from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.pipeline.core.events import DomainEvent


@dataclass
class QuotaSnapshot:
    """租户配额快照（请求开始时固化，避免并发读写）"""
    max_documents: int
    max_storage_bytes: int
    max_api_calls_per_day: int
    used_documents: int
    used_storage_bytes: int
    used_api_calls_today: int

    def has_api_quota(self) -> bool:
        return self.used_api_calls_today < self.max_api_calls_per_day

    def has_storage_quota(self, needed_bytes: int = 0) -> bool:
        return self.used_storage_bytes + needed_bytes <= self.max_storage_bytes


@dataclass
class TenantConfig:
    """租户级运行时配置（从 Nacos 加载）"""
    tenant_id: str
    ingestion_strategy: str = "standard"
    retrieval_strategy: str = "hybrid"
    embedding_provider: str = "openai_embedding"
    llm_provider: str = "openai_llm"
    chunk_size: int = 512
    chunk_overlap: int = 64
    top_k: int = 10
    rerank_enabled: bool = True


@dataclass
class ProcessingContext:
    """
    贯穿整条处理链的请求上下文。

    设计原则：
    - tenant_id / trace_id 是不可变的安全边界
    - metadata 是可扩展区，Stage 间通过 with_metadata() 传递中间结果
    - events 列表记录领域事件，Pipeline 结束后统一广播
    """
    tenant_id: str
    document_id: str
    task_id: str
    trace_id: str
    quota: QuotaSnapshot
    config: TenantConfig
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list["DomainEvent"] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        tenant_id: str,
        document_id: str,
        quota: QuotaSnapshot,
        config: TenantConfig,
        task_id: str | None = None,
        trace_id: str | None = None,
    ) -> "ProcessingContext":
        return cls(
            tenant_id=tenant_id,
            document_id=document_id,
            task_id=task_id or str(uuid.uuid4()),
            trace_id=trace_id or str(uuid.uuid4()),
            quota=quota,
            config=config,
        )

    def with_metadata(self, **kwargs: Any) -> "ProcessingContext":
        """不可变更新：返回新 Context，原 Context 不变"""
        return dataclasses.replace(self, metadata={**self.metadata, **kwargs})

    def emit(self, event: "DomainEvent") -> "ProcessingContext":
        """追加领域事件，返回新 Context"""
        return dataclasses.replace(self, events=[*self.events, event])

    def get(self, key: str, default: Any = None) -> Any:
        return self.metadata.get(key, default)
