from __future__ import annotations

from types import SimpleNamespace

from app.domain.retrieval import RetrievalResult, SearchHit
from app.pipeline.core.context import TenantConfig
from app.workflows import rag_orchestrator as module
from app.workflows.rag_orchestrator import RAGOrchestrator

DOC1 = "11111111-1111-4111-8111-111111111111"
DOC2 = "22222222-2222-4222-8222-222222222222"


async def test_retrieve_threads_scope_and_drops_out_of_scope_hits(monkeypatch) -> None:
    captured = {}

    class _Pipeline:
        async def run(self, ctx, search_ctx):
            captured["query"] = search_ctx.query
            return [
                SearchHit("chunk-1", DOC1, 0, 0.9, source="vector"),
                SearchHit("chunk-2", DOC2, 0, 0.8, source="keyword"),
            ]

    class _Strategy:
        def build_pipeline_with_hooks(self, name, config):
            return _Pipeline()

    class _ChunkRepo:
        async def get_chunks_by_ids(self, chunk_ids, tenant_id):
            captured["chunk_ids"] = chunk_ids
            return [
                {
                    "chunk_id": "chunk-1",
                    "document_id": DOC1,
                    "content": "允许的文档内容",
                }
            ]

    orchestrator = RAGOrchestrator()
    orchestrator._chunk_repo = _ChunkRepo()

    async def fake_config(tenant_id):
        return TenantConfig(tenant_id=tenant_id)

    monkeypatch.setattr(orchestrator._tenant_config_svc, "get_or_default", fake_config)
    monkeypatch.setattr(module.registry, "get_strategy", lambda name: _Strategy())

    result = await orchestrator.retrieve(
        query_text="合同金额",
        tenant_id="tenant-1",
        space_id="space-1",
        document_ids=[DOC1],
    )

    assert captured["query"].document_ids == [DOC1]
    assert [hit.document_id for hit in result.hits] == [DOC1]
    assert captured["chunk_ids"] == ["chunk-1"]


async def test_document_scoped_chat_bypasses_semantic_cache(monkeypatch) -> None:
    cache_get_calls = 0

    async def fake_cache_get(*args, **kwargs):
        nonlocal cache_get_calls
        cache_get_calls += 1
        return SimpleNamespace(answer="错误的缓存答案", citations=[])

    orchestrator = RAGOrchestrator()

    async def fake_retrieve(**kwargs):
        assert kwargs["document_ids"] == [DOC1]
        return RetrievalResult(query_text=kwargs["query_text"], hits=[], chunks=[])

    async def fake_stream_generate(**kwargs):
        yield "附件答案"

    monkeypatch.setattr(module._cache, "get", fake_cache_get)
    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "stream_generate", fake_stream_generate)

    output = [
        item
        async for item in orchestrator.chat(
            query="总结这个文件",
            tenant_id="tenant-1",
            history=[],
            space_id="space-1",
            document_ids=[DOC1],
        )
    ]

    assert output == ["附件答案"]
    assert cache_get_calls == 0


async def test_unscoped_chat_keeps_existing_cache_behavior(monkeypatch) -> None:
    orchestrator = RAGOrchestrator()

    async def fake_cache_get(*args, **kwargs):
        return SimpleNamespace(
            answer="空间缓存答案",
            citations=[{"doc_id": DOC2}],
        )

    async def unexpected_retrieve(**kwargs):
        raise AssertionError("普通首轮聊天命中缓存后不应执行检索")

    monkeypatch.setattr(module._cache, "get", fake_cache_get)
    monkeypatch.setattr(orchestrator, "retrieve", unexpected_retrieve)

    output = [
        item
        async for item in orchestrator.chat(
            query="已有问题",
            tenant_id="tenant-1",
            history=[],
            space_id="space-1",
        )
    ]

    assert output == ["空间缓存答案", [{"doc_id": DOC2}]]
