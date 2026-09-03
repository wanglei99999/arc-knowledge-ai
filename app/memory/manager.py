"""
MemoryManager — 三层记忆的统一读取入口。

职责：
- 从 DB 加载工作记忆（Layer 1/2）
- 搜索语义记忆（Layer 3）
- 保存用户消息
"""

from __future__ import annotations

import uuid

from app.config.settings import settings
from app.domain.memory import Memory, WorkingMemory
from app.infrastructure.milvus.memory_collection import search_memory_vectors
from app.infrastructure.postgres.repositories.memory_repo import MemoryRepository
from app.infrastructure.postgres.repositories.session_repo import SessionRepository
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.registry import registry
from app.providers.base import EmbeddingProvider


def _make_ctx(tenant_id: str) -> ProcessingContext:
    return ProcessingContext(
        tenant_id=tenant_id,
        document_id="",
        task_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        quota=QuotaSnapshot(
            max_documents=0,
            max_storage_bytes=0,
            max_api_calls_per_day=999999,
            used_documents=0,
            used_storage_bytes=0,
            used_api_calls_today=0,
        ),
        config=TenantConfig(tenant_id=tenant_id),
    )


class MemoryManager:
    def __init__(self) -> None:
        self._session_repo = SessionRepository()
        self._memory_repo = MemoryRepository()

    async def load_working_memory(
        self, session_id: str, tenant_id: str, user_id: str
    ) -> WorkingMemory:
        """
        加载工作记忆（Layer 1 + Layer 2）。

        策略：
        - message_count <= last_n + 2 → 全部消息作为 recent，anchor 为空
        - message_count > last_n + 2  → First-2 作 anchor，Last-N 作 recent
        """
        last_n = settings.memory_last_n

        session = await self._session_repo.get_by_id(session_id, tenant_id, user_id)
        if session is None:
            return WorkingMemory(anchor=[], summary=None, recent=[], message_count=0)

        if session.message_count <= last_n + 2:
            # 消息总量在窗口内，全部加载
            all_msgs = await self._session_repo.get_all_messages(session_id, tenant_id, user_id)
            return WorkingMemory(
                anchor=[],
                summary=None,
                recent=all_msgs,
                message_count=session.message_count,
            )
        else:
            # 分段加载
            anchor = await self._session_repo.get_first_messages(
                session_id, tenant_id, user_id, n=2
            )
            recent = await self._session_repo.get_last_messages(
                session_id, tenant_id, user_id, n=last_n
            )
            return WorkingMemory(
                anchor=anchor,
                summary=session.summary,
                recent=recent,
                message_count=session.message_count,
            )

    async def load_semantic_memories(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        embedding_provider_name: str,
    ) -> list[Memory]:
        """搜索与当前 query 最相关的语义记忆（Layer 3）"""
        embed: EmbeddingProvider = registry.get_provider(embedding_provider_name)
        ctx = _make_ctx(tenant_id)
        embeddings = await embed.embed(ctx, [query])
        query_embedding = embeddings[0]

        hits = await search_memory_vectors(
            query_embedding=query_embedding,
            tenant_id=tenant_id,
            user_id=user_id,
            top_k=settings.memory_top_k,
            score_threshold=settings.memory_similarity_threshold,
        )
        if not hits:
            return []

        memory_ids = [h["memory_id"] for h in hits]
        return await self._memory_repo.get_by_ids(memory_ids, tenant_id, user_id)

    async def save_user_message(
        self,
        session_id: str,
        tenant_id: str,
        user_id: str,
        content: str,
    ) -> None:
        await self._session_repo.add_message(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            role="user",
            content=content,
        )
