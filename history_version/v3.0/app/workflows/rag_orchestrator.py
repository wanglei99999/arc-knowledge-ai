from __future__ import annotations

import uuid
from typing import AsyncIterator

from app.domain.retrieval import RetrievalQuery, RetrievalResult, SearchContext
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, LLMProvider

# 系统 Prompt 模板
_SYSTEM_PROMPT = """你是一个知识库问答助手。请根据以下检索到的文档片段回答用户问题。
回答要准确、简洁，如果文档中没有相关信息，请如实说明，不要编造内容。

【参考文档】
{context}
"""

_FAKE_QUOTA = QuotaSnapshot(
    max_documents=10000,
    max_storage_bytes=10 * 1024 ** 3,
    max_api_calls_per_day=100000,
    used_documents=0,
    used_storage_bytes=0,
    used_api_calls_today=0,
)


class RAGOrchestrator:
    """
    RAG 全链路协调器（非 Temporal，同步检索 + 流式生成）。

    职责：
    1. 调用 Retrieval Pipeline 完成混合检索
    2. 从 PostgreSQL 拉取 chunk 文本
    3. 构建 Prompt，调用 LLMProvider 生成回答（流式 / 非流式）
    """

    def __init__(self) -> None:
        self._chunk_repo = ChunkRepository()

    def _make_ctx(self, tenant_id: str, config: TenantConfig) -> ProcessingContext:
        return ProcessingContext.create(
            tenant_id=tenant_id,
            document_id="",          # 检索阶段无单一 document_id
            quota=_FAKE_QUOTA,
            config=config,
            trace_id=str(uuid.uuid4()),
        )

    def _get_llm_provider(self, ctx: ProcessingContext) -> LLMProvider:
        return registry.get_provider(ctx.config.llm_provider)  # type: ignore[return-value]

    async def retrieve(
        self,
        query_text: str,
        tenant_id: str,
        top_k: int = 10,
        score_threshold: float = 0.5,
    ) -> RetrievalResult:
        """执行混合检索，返回带文本的检索结果。"""
        config = TenantConfig(tenant_id=tenant_id)
        ctx = self._make_ctx(tenant_id, config)

        strategy = registry.get_strategy(config.retrieval_strategy)
        pipeline = strategy.build_pipeline("query", config)

        query = RetrievalQuery(
            query_text=query_text,
            tenant_id=tenant_id,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        search_ctx = SearchContext(query=query)
        hits = await pipeline.run(ctx, search_ctx)   # → list[SearchHit]

        # 从 PostgreSQL 拉取 chunk 文本
        chunk_ids = [h.chunk_id for h in hits]
        chunks = await self._chunk_repo.get_chunks_by_ids(chunk_ids, tenant_id)

        return RetrievalResult(
            query_text=query_text,
            hits=hits,
            chunks=chunks,
        )

    async def generate(
        self,
        result: RetrievalResult,
        history: list[ChatMessage],
        tenant_id: str,
    ) -> str:
        """非流式生成（用于测试 / 批处理）。"""
        config = TenantConfig(tenant_id=tenant_id)
        ctx = self._make_ctx(tenant_id, config)
        messages = self._build_messages(result, history)
        provider = self._get_llm_provider(ctx)
        return await provider.generate(ctx, messages)

    async def stream_generate(
        self,
        result: RetrievalResult,
        history: list[ChatMessage],
        tenant_id: str,
    ) -> AsyncIterator[str]:
        """流式生成，yield token，供 SSE 推送。"""
        config = TenantConfig(tenant_id=tenant_id)
        ctx = self._make_ctx(tenant_id, config)
        messages = self._build_messages(result, history)
        provider = self._get_llm_provider(ctx)
        async for token in provider.stream_generate(ctx, messages):
            yield token

    def _build_messages(
        self,
        result: RetrievalResult,
        history: list[ChatMessage],
    ) -> list[ChatMessage]:
        system = ChatMessage(
            role="system",
            content=_SYSTEM_PROMPT.format(context=result.context_text),
        )
        user = ChatMessage(role="user", content=result.query_text)
        return [system, *history, user]
