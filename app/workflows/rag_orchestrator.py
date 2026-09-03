from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing

from opentelemetry.trace import StatusCode

from app.config.settings import settings
from app.domain.metadata_filter import MetadataFilter
from app.domain.retrieval import (
    RetrievalQuery,
    RetrievalResult,
    SearchContext,
    normalize_document_ids,
)
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.infrastructure.redis.semantic_cache import cache as _cache
from app.infrastructure.telemetry.extractors import KIND_KEY, chunk_doc_attrs
from app.infrastructure.telemetry.otel import get_tracer
from app.infrastructure.telemetry.spans import activate_span, traced_block
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, LLMProvider
from app.providers.llm.model_hub import model_hub
from app.providers.llm.traced_llm import PROMPT_TOKENS_KEY
from app.services.tenant_config_service import TenantConfigService
from app.utils.tokenizer import count_tokens

logger = logging.getLogger(__name__)

# 系统 Prompt 模板
_SYSTEM_PROMPT = """你是一个知识库问答助手。请根据以下检索到的文档片段回答用户问题。
回答要准确、简洁，如果文档中没有相关信息，请如实说明，不要编造内容。

【参考文档】
{context}
"""

_FAKE_QUOTA = QuotaSnapshot(
    max_documents=10000,
    max_storage_bytes=10 * 1024**3,
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
            document_id="",  # 检索阶段无单一 document_id
            quota=_FAKE_QUOTA,
            config=config,
            trace_id=str(uuid.uuid4()),
        )

    def _get_llm_provider(self, ctx: ProcessingContext) -> LLMProvider:
        return model_hub.get_provider(ctx)

    async def build_llm(
        self, tenant_id: str, model: str | None = None
    ) -> tuple[ProcessingContext, LLMProvider]:
        """加载租户配置 → 构建 ctx → 经 ModelHub 取带熔断/校验的 LLM。

        LLM 选择的唯一入口：检索生成与记忆链路共用，主模型取自租户配置
        （非 fallback 备胎），避免 provider 选择逻辑在多处漂移。
        """
        config = await self._tenant_config_svc.get_or_default(tenant_id)
        if model:
            config = dataclasses.replace(config, default_llm_model=model)
        ctx = self._make_ctx(tenant_id, config)
        return ctx, self._get_llm_provider(ctx)

    async def retrieve(
        self,
        query_text: str,
        tenant_id: str,
        space_id: str = "default",
        top_k: int = 10,
        score_threshold: float = 0.5,
        model: str | None = None,
        metadata_filters: list[MetadataFilter] | None = None,
        document_ids: list[str] | None = None,
    ) -> RetrievalResult:
        """执行混合检索，返回带文本的检索结果。"""
        config = await self._tenant_config_svc.get_or_default(tenant_id)
        if model:
            config = dataclasses.replace(config, default_llm_model=model)
        ctx = self._make_ctx(tenant_id, config)

        strategy = registry.get_strategy(config.retrieval_strategy)
        # with_hooks 版本:否则 strategy.hooks 声明形同虚设,检索链路无 trace
        pipeline = strategy.build_pipeline_with_hooks("query", config)

        canonical_document_ids = normalize_document_ids(document_ids)
        query = RetrievalQuery(
            query_text=query_text,
            tenant_id=tenant_id,
            space_id=space_id,
            top_k=top_k,
            score_threshold=score_threshold,
            metadata_filters=metadata_filters or [],
            document_ids=canonical_document_ids,
        )
        search_ctx = SearchContext(query=query)
        hits = await pipeline.run(ctx, search_ctx)  # → list[SearchHit]
        if canonical_document_ids:
            allowed = set(canonical_document_ids)
            hits = [hit for hit in hits if hit.document_id in allowed]

        # 从 PostgreSQL 拉取 chunk 文本
        chunk_ids = [h.chunk_id for h in hits]
        with traced_block("chunk.fetch") as span:
            chunks = await self._chunk_repo.get_chunks_by_ids(chunk_ids, tenant_id)
            try:
                # 带文本的最终文档列表挂在这——文本真实存在之处(helper 复用 _DOC_LIMIT/截断)
                span.set_attributes(chunk_doc_attrs(chunks))
            except Exception:
                logger.warning("chunk.fetch span attributes failed", exc_info=True)

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
        ctx, provider = await self.build_llm(tenant_id, model)
        messages, prompt_tokens = self._build_messages(result, history)
        ctx.metadata[PROMPT_TOKENS_KEY] = prompt_tokens  # 观测层复用,避免二次编码
        return await provider.generate(ctx, messages)

    async def stream_generate(
        self,
        result: RetrievalResult,
        history: list[ChatMessage],
        tenant_id: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """流式生成，yield token，供 SSE 推送。"""
        ctx, provider = await self.build_llm(tenant_id, model)
        messages, prompt_tokens = self._build_messages(result, history)
        ctx.metadata[PROMPT_TOKENS_KEY] = prompt_tokens  # 观测层复用,避免二次编码
        # aclosing:消费方中途放弃时逐层确定性关闭,span/连接不等 GC
        async with aclosing(provider.stream_generate(ctx, messages)) as stream:
            async for token in stream:
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
        metadata_filters: list[MetadataFilter] | None = None,
        document_ids: list[str] | None = None,
    ) -> AsyncIterator[str | list]:
        """
        带语义缓存的流式问答门面。

        history 为空（首轮）时走缓存路径；有历史记录时直接走 RAG，
        避免多轮上下文污染缓存答案。
        """
        # rag.chat 根 span。上下文纪律:只在"激活段"(无 yield 的顺序 await)内
        # attach 为 current,任何 yield 之前必已 detach——避免泄漏给流式消费方。
        span = get_tracer("arc.rag").start_span("rag.chat")
        span.set_attribute(KIND_KEY, "CHAIN")
        span.set_attribute("arc.tenant_id", tenant_id)
        if settings.otel_capture_content:
            span.set_attribute("input.value", query)
        try:
            canonical_document_ids = normalize_document_ids(document_ids)
            cached = None
            first: str | None = None
            agen: AsyncIterator[str] | None = None
            # ── 激活段:缓存/检索/首 token,产生的子 span 全部挂到 rag.chat 下 ──
            with activate_span(span):
                # 带 metadata_filters 时绕过语义缓存：缓存键不含 filters，
                # 否则同一 query 不同过滤条件会命中错误的缓存答案。
                use_cache = not history and not metadata_filters and not canonical_document_ids
                if use_cache:
                    with traced_block("cache.lookup") as cache_span:
                        cached = await _cache.get(query, tenant_id, space_id)
                        cache_span.set_attribute("arc.cache.hit", cached is not None)
                if not cached:
                    result = await self.retrieve(
                        query_text=query,
                        tenant_id=tenant_id,
                        space_id=space_id,
                        top_k=top_k,
                        score_threshold=score_threshold,
                        model=model,
                        metadata_filters=metadata_filters,
                        document_ids=canonical_document_ids,
                    )
                    history_messages = [
                        ChatMessage(role=m["role"], content=m["content"]) for m in history
                    ]
                    agen = self.stream_generate(
                        result=result,
                        history=history_messages,
                        tenant_id=tenant_id,
                        model=model,
                    )
                    # 预取首 token:llm.generate span 在此创建,认 rag.chat 当爹
                    first = await anext(agen, None)

            # ── 流式段:上下文已还原,可以放心 yield ──
            if cached:
                yield cached.answer
                if cached.citations:
                    yield cached.citations
                return

            tokens: list[str] = [] if first is None else [first]
            if first is not None:
                yield first
            assert agen is not None
            async with aclosing(agen):
                async for token in agen:
                    tokens.append(token)
                    yield token

            citations = self._build_citations(result)
            if citations:
                yield citations

            if use_cache:
                asyncio.create_task(
                    _cache.set(query, "".join(tokens), citations, tenant_id, space_id)
                )
        except BaseException as e:
            # 含 GeneratorExit/CancelledError:中断与失败都要如实标记,不吞异常
            span.set_status(StatusCode.ERROR, str(e))
            raise
        finally:
            span.end()

    @staticmethod
    def _build_citations(result: RetrievalResult) -> list[dict]:
        chunk_map = {c["chunk_id"]: c for c in result.chunks}
        return [
            {
                "doc_id": h.document_id,
                "chunk_id": h.chunk_id,
                "doc_name": chunk_map.get(h.chunk_id, {}).get("original_name") or h.document_id,
                "chunk_index": h.chunk_index,
                "content": chunk_map.get(h.chunk_id, {}).get("content", ""),
                "score": round(h.score, 4),
                "source": h.source,
            }
            for h in result.hits
            if h.chunk_id in chunk_map
        ]

    def _build_messages(
        self,
        result: RetrievalResult,
        history: list[ChatMessage],
    ) -> tuple[list[ChatMessage], int]:
        """返回 (messages, prompt_tokens)。

        prompt_tokens 是预算计算的副产品——这里已经把全部文本编码过一遍,
        带出去给观测层复用,避免 TracedLLMProvider 对同一 prompt 再编码一次
        (28k token 级文本的二次编码是热路径上纯冗余的几十毫秒同步阻塞)。
        """
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

        context, context_tokens = self._truncate_context(result, max_tokens=max(budget, 0))
        system = ChatMessage(
            role="system",
            content=_SYSTEM_PROMPT.format(context=context),
        )
        user = ChatMessage(role="user", content=result.query_text)
        prompt_tokens = template_tokens + context_tokens + history_tokens + query_tokens
        return [system, *history, user], prompt_tokens

    @staticmethod
    def _truncate_context(result: RetrievalResult, max_tokens: int) -> tuple[str, int]:
        """按检索相关性顺序加入 chunk，超出 token 预算时截止。返回 (文本, 实际用掉的 token 数)。"""
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

        return "\n\n".join(parts), used
