"""QueryRewriteStage 单元测试"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.domain.retrieval import RetrievalQuery, SearchContext
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.stages.retrieval.query_rewrite_stage import QueryRewriteStage
from app.providers.base import ChatMessage, HealthStatus, LLMProvider

# ── Fake LLM ──────────────────────────────────────────────────────────────────


class FakeLLMProvider(LLMProvider):
    provider_id = "fake_llm"

    def __init__(self, response: str) -> None:
        self._response = response

    async def health_check(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def generate(self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs) -> str:
        return self._response

    async def stream_generate(
        self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs
    ) -> AsyncIterator[str]:
        yield self._response


class ErrorLLMProvider(LLMProvider):
    provider_id = "error_llm"

    async def health_check(self) -> HealthStatus:
        return HealthStatus.UNHEALTHY

    async def generate(self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs) -> str:
        raise RuntimeError("LLM unavailable")

    async def stream_generate(
        self, ctx: ProcessingContext, messages: list[ChatMessage], **kwargs
    ) -> AsyncIterator[str]:
        raise RuntimeError("LLM unavailable")
        yield


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_ctx(query_rewrite_enabled: bool = True) -> ProcessingContext:
    quota = QuotaSnapshot(
        max_documents=100,
        max_storage_bytes=1024,
        max_api_calls_per_day=1000,
        used_documents=0,
        used_storage_bytes=0,
        used_api_calls_today=0,
    )
    config = TenantConfig(
        tenant_id="test-tenant",
        query_rewrite_enabled=query_rewrite_enabled,
    )
    return ProcessingContext.create(
        tenant_id="test-tenant",
        document_id="",
        quota=quota,
        config=config,
    )


def _search_ctx(query_text: str = "向量数据库怎么选") -> SearchContext:
    return SearchContext(query=RetrievalQuery(query_text=query_text, tenant_id="test-tenant"))


# ── 正常路径 ───────────────────────────────────────────────────────────────────


async def test_valid_query_expands_and_marks_valid() -> None:
    llm_response = json.dumps(
        {
            "is_knowledge_query": True,
            "expanded_queries": [
                "如何选择向量数据库",
                "向量数据库选型对比",
                "主流向量数据库优缺点",
            ],
        }
    )
    stage = QueryRewriteStage(provider=FakeLLMProvider(llm_response))
    ctx = _make_ctx()
    result = await stage._execute(ctx, _search_ctx())

    assert result.query.intent_is_valid is True
    assert len(result.query.expanded_queries) == 3
    assert "如何选择向量数据库" in result.query.expanded_queries


async def test_chitchat_marks_invalid_and_empty_queries() -> None:
    llm_response = json.dumps(
        {
            "is_knowledge_query": False,
            "expanded_queries": [],
        }
    )
    stage = QueryRewriteStage(provider=FakeLLMProvider(llm_response))
    ctx = _make_ctx()
    result = await stage._execute(ctx, _search_ctx("你好"))

    assert result.query.intent_is_valid is False
    assert result.query.expanded_queries == []


async def test_original_query_text_preserved() -> None:
    llm_response = json.dumps(
        {
            "is_knowledge_query": True,
            "expanded_queries": ["变体1", "变体2", "变体3"],
        }
    )
    stage = QueryRewriteStage(provider=FakeLLMProvider(llm_response))
    ctx = _make_ctx()
    original = _search_ctx("RAG 是什么")
    result = await stage._execute(ctx, original)

    assert result.query.query_text == "RAG 是什么"


# ── 降级路径 ───────────────────────────────────────────────────────────────────


async def test_llm_failure_falls_back_to_passthrough() -> None:
    stage = QueryRewriteStage(provider=ErrorLLMProvider())
    ctx = _make_ctx()
    original = _search_ctx("some query")
    result = await stage._execute(ctx, original)

    # passthrough：intent_is_valid 保持默认 True，expanded_queries 为空
    assert result.query.intent_is_valid is True
    assert result.query.expanded_queries == []
    assert result.query.query_text == "some query"


async def test_invalid_json_falls_back_to_passthrough() -> None:
    stage = QueryRewriteStage(provider=FakeLLMProvider("this is not json"))
    ctx = _make_ctx()
    result = await stage._execute(ctx, _search_ctx())

    assert result.query.intent_is_valid is True
    assert result.query.expanded_queries == []


async def test_empty_expanded_queries_filtered() -> None:
    # LLM 返回含空字符串的 expanded_queries
    llm_response = json.dumps(
        {
            "is_knowledge_query": True,
            "expanded_queries": ["valid query", "", "  ", "another valid"],
        }
    )
    stage = QueryRewriteStage(provider=FakeLLMProvider(llm_response))
    ctx = _make_ctx()
    result = await stage._execute(ctx, _search_ctx())

    assert result.query.expanded_queries == ["valid query", "another valid"]


# ── 开关控制 ───────────────────────────────────────────────────────────────────


async def test_disabled_returns_passthrough_without_llm_call() -> None:
    stage = QueryRewriteStage(provider=ErrorLLMProvider())
    ctx = _make_ctx(query_rewrite_enabled=False)
    original = _search_ctx()
    result = await stage._execute(ctx, original)

    # 直接返回原 SearchContext，不调用 LLM（ErrorLLMProvider 若被调用会抛异常）
    assert result is original
