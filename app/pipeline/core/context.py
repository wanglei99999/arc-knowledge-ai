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
    max_spend_per_day: float = 0.0  # 0 = 不限制
    used_spend_today: float = 0.0

    def has_api_quota(self) -> bool:
        return self.used_api_calls_today < self.max_api_calls_per_day

    def has_storage_quota(self, needed_bytes: int = 0) -> bool:
        # 与 has_api_quota / has_spend_quota 一致：used 达到上限即视为耗尽（严格 <）
        return self.used_storage_bytes + needed_bytes < self.max_storage_bytes

    def has_spend_quota(self) -> bool:
        if self.max_spend_per_day <= 0:
            return True
        return self.used_spend_today < self.max_spend_per_day


@dataclass
class TenantConfig:
    """租户级运行时配置（从 DB / Nacos 加载）"""

    tenant_id: str
    ingestion_strategy: str = "standard"
    retrieval_strategy: str = "hybrid"
    embedding_provider: str = "openai_embedding"
    parser_provider: str = "unstructured_parser"
    llm_provider: str = "openai_llm"
    default_llm_model: str = ""  # 空字符串 = 使用 Provider 自己的 settings 默认值
    allowed_models: list[str] = field(default_factory=list)  # 空列表 = 不限制
    chunk_size: int = 400
    chunk_overlap: int = 50
    top_k: int = 10
    rerank_enabled: bool = True
    query_rewrite_enabled: bool = True
    rerank_provider: str = "infinity_rerank"


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
    events: list[DomainEvent] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        tenant_id: str,
        document_id: str,
        quota: QuotaSnapshot,
        config: TenantConfig,
        task_id: str | None = None,
        trace_id: str | None = None,
    ) -> ProcessingContext:
        return cls(
            tenant_id=tenant_id,
            document_id=document_id,
            task_id=task_id or str(uuid.uuid4()),
            trace_id=trace_id or str(uuid.uuid4()),
            quota=quota,
            config=config,
        )

    def with_metadata(self, **kwargs: Any) -> ProcessingContext:
        """不可变更新：返回新 Context，原 Context 不变"""
        return dataclasses.replace(self, metadata={**self.metadata, **kwargs})

    def emit(self, event: DomainEvent) -> ProcessingContext:
        """追加领域事件，返回新 Context"""
        return dataclasses.replace(self, events=[*self.events, event])

    def get(self, key: str, default: Any = None) -> Any:
        return self.metadata.get(key, default)
