from __future__ import annotations

import asyncio
import dataclasses
import uuid
from typing import AsyncIterator

from app.domain.retrieval import RetrievalQuery, RetrievalResult, SearchContext
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.infrastructure.redis.semantic_cache import cache as _cache
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, LLMProvider
from app.providers.llm.model_hub import model_hub
from app.services.tenant_config_service import TenantConfigService
from app.utils.tokenizer import count_tokens

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
        self._tenant_config_svc = TenantConfigService()

    def _make_ctx(self, tenant_id: str, config: TenantConfig) -> ProcessingContext:
        return ProcessingContext.create(
            tenant_id=tenant_id,
            document_id="",          # 检索阶段无单一 document_id
            quota=_FAKE_QUOTA,
            config=config,
            trace_id=str(uuid.uuid4()),
        )

    def _get_llm_provider(self, ctx: ProcessingContext) -> LLMProvider:
        return model_hub.get_provider(ctx)

    async def retrieve(
        self,
        query_text: str,
        tenant_id: str,
        space_id: str = "default",
        top_k: int = 10,
        score_threshold: float = 0.5,
        model: str | None = None,
    ) -> RetrievalResult:
        """执行混合检索，返回带文本的检索结果。"""
        config = await self._tenant_config_svc.get_or_default(tenant_id)
        if model:
            config = dataclasses.replace(config, default_llm_model=model)
        ctx = self._make_ctx(tenant_id, config)

        strategy = registry.get_strategy(config.retrieval_strategy)
        pipeline = strategy.build_pipeline("query", config)

        query = RetrievalQuery(
            query_text=query_text,
            tenant_id=tenant_id,
            space_id=space_id,
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
        model: str | None = None,
    ) -> str:
        """非流式生成（用于测试 / 批处理）。"""
        config = await self._tenant_config_svc.get_or_default(tenant_id)
        if model:
            config = dataclasses.replace(config, default_llm_model=model)
        ctx = self._make_ctx(tenant_id, config)
        messages = self._build_messages(result, history)
        provider = self._get_llm_provider(ctx)
        return await provider.generate(ctx, messages)

    async def stream_generate(
        self,
        result: RetrievalResult,
        history: list[ChatMessage],
        tenant_id: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """流式生成，yield token，供 SSE 推送。"""
        config = await self._tenant_config_svc.get_or_default(tenant_id)
        if model:
            config = dataclasses.replace(config, default_llm_model=model)
        ctx = self._make_ctx(tenant_id, config)
        messages = self._build_messages(result, history)
        provider = self._get_llm_provider(ctx)
        async for token in provider.stream_generate(ctx, messages):
            yield token

    async def chat(
        self,
        query: str,
        tenant_id: str,
        history: list[dict],
        space_id: str = "default",
        top_k: int = 10,
        score_threshold: float = 0.5,
        model: str | None = None,
    ) -> AsyncIterator[str | list]:
        """
        带语义缓存的流式问答门面。

        history 为空（首轮）时走缓存路径；有历史记录时直接走 RAG，
        避免多轮上下文污染缓存答案。
        """
        if not history:
            cached = await _cache.get(query, tenant_id, space_id)
            if cached:
                yield cached.answer
                if cached.citations:
                    yield cached.citations
                return

        result = await self.retrieve(
            query_text=query,
            tenant_id=tenant_id,
            space_id=space_id,
            top_k=top_k,
            score_threshold=score_threshold,
            model=model,
        )
        history_messages = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in history
        ]

        tokens: list[str] = []
        async for token in self.stream_generate(
            result=result,
            history=history_messages,
            tenant_id=tenant_id,
            model=model,
        ):
            tokens.append(token)
            yield token

        citations = self._build_citations(result)
        if citations:
            yield citations

        if not history:
            asyncio.create_task(
                _cache.set(query, "".join(tokens), citations, tenant_id, space_id)
            )

    @staticmethod
    def _build_citations(result: RetrievalResult) -> list[dict]:
        chunk_map = {c["chunk_id"]: c for c in result.chunks}
        return [
            {
                "doc_id":      h.document_id,
                "chunk_id":    h.chunk_id,
                "doc_name":    chunk_map.get(h.chunk_id, {}).get("original_name") or h.document_id,
                "chunk_index": h.chunk_index,
                "content":     chunk_map.get(h.chunk_id, {}).get("content", ""),
                "score":       round(h.score, 4),
                "source":      h.source,
            }
            for h in result.hits
            if h.chunk_id in chunk_map
        ]

    def _build_messages(
        self,
        result: RetrievalResult,
        history: list[ChatMessage],
    ) -> list[ChatMessage]:
        from app.config.settings import settings

        # 计算可用于 chunk context 的 token 预算
        history_tokens = sum(count_tokens(m.content) for m in history)
        query_tokens = count_tokens(result.query_text)
        template_tokens = count_tokens(_SYSTEM_PROMPT.replace("{context}", ""))
        budget = (
            settings.llm_context_window
            - settings.llm_context_reserved
            - history_tokens
            - query_tokens
            - template_tokens
        )

        context = self._truncate_context(result, max_tokens=max(budget, 0))
        system = ChatMessage(
            role="system",
            content=_SYSTEM_PROMPT.format(context=context),
        )
        user = ChatMessage(role="user", content=result.query_text)
        return [system, *history, user]

    @staticmethod
    def _truncate_context(result: RetrievalResult, max_tokens: int) -> str:
        """按检索相关性顺序加入 chunk，超出 token 预算时截止。"""
        chunk_map = {c["chunk_id"]: c for c in result.chunks}
        parts: list[str] = []
        used = 0

        for i, hit in enumerate(result.hits, 1):
            chunk = chunk_map.get(hit.chunk_id)
            if not chunk:
                continue
            doc_name = chunk.get("original_name") or hit.document_id
            part = f"[{i}] 来源：{doc_name}\n{chunk['content']}"
            part_tokens = count_tokens(part)
            if used + part_tokens > max_tokens:
                break
            parts.append(part)
            used += part_tokens

        return "\n\n".join(parts)
