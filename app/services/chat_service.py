from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

from app.memory.assembler import ContextAssembler
from app.memory.extractor import MemoryExtractor
from app.memory.manager import MemoryManager
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.registry import registry
from app.providers.base import LLMProvider
from app.workflows.rag_orchestrator import RAGOrchestrator

logger = logging.getLogger(__name__)

_orchestrator = RAGOrchestrator()
_manager      = MemoryManager()
_assembler    = ContextAssembler()
_extractor    = MemoryExtractor()

_FAKE_QUOTA = QuotaSnapshot(
    max_documents=10000, max_storage_bytes=10 * 1024 ** 3,
    max_api_calls_per_day=100000, used_documents=0,
    used_storage_bytes=0, used_api_calls_today=0,
)


@dataclass
class ChatRequest:
    query: str
    tenant_id: str
    space_id: str
    user_id: str | None = None           # require_user() 填入
    session_id: str | None = None        # 有 session 时启用三层记忆
    llm_provider_name: str = "openai_llm"
    embedding_provider_name: str = "openai_embedding"
    history: list[dict] = field(default_factory=list)   # 无 session 时的后备历史
    top_k: int = 10
    score_threshold: float = 0.5
    model: str | None = None             # 请求级模型覆盖（优先于租户配置）


class ChatService:
    """
    RAG 问答服务。

    有 session_id + user_id → 三层记忆流程（工作记忆 + 语义记忆 + RAG）
    无 session_id            → 原有简单流程（history list + RAG），向后兼容
    """

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[str | list]:
        if req.session_id and req.user_id:
            async for item in self._stream_with_memory(req):
                yield item
        else:
            async for item in self._stream_simple(req):
                yield item

    @staticmethod
    def _build_citations(rag_result) -> list[dict]:
        chunk_map = {c["chunk_id"]: c for c in rag_result.chunks}
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
            for h in rag_result.hits
            if h.chunk_id in chunk_map
        ]

    # ── 三层记忆流程 ──────────────────────────────────────────────────────────

    async def _stream_with_memory(self, req: ChatRequest) -> AsyncIterator[str | list]:
        assert req.session_id and req.user_id

        # 1. 保存用户消息（先写，使 Last-N 包含本条）
        await _manager.save_user_message(
            session_id=req.session_id,
            tenant_id=req.tenant_id,
            user_id=req.user_id,
            content=req.query,
        )

        # 2. 并行加载：工作记忆 + 语义记忆 + RAG 检索
        working_memory, semantic_memories, rag_result = await asyncio.gather(
            _manager.load_working_memory(req.session_id, req.tenant_id, req.user_id),
            _manager.load_semantic_memories(
                tenant_id=req.tenant_id,
                user_id=req.user_id,
                query=req.query,
                embedding_provider_name=req.embedding_provider_name,
            ),
            _orchestrator.retrieve(
                query_text=req.query,
                tenant_id=req.tenant_id,
                top_k=req.top_k,
                score_threshold=req.score_threshold,
                model=req.model,
            ),
        )

        # 3. 获取 LLM Provider
        llm: LLMProvider = registry.get_provider(req.llm_provider_name)
        context_window = llm.get_context_window()

        # 4. 组装 context
        messages = _assembler.build(
            context_window=context_window,
            working_memory=working_memory,
            semantic_memories=semantic_memories,
            rag_text=rag_result.context_text,
        )

        # 5. 流式生成，同步收集完整响应
        ctx = ProcessingContext.create(
            tenant_id=req.tenant_id,
            document_id="",
            quota=_FAKE_QUOTA,
            config=TenantConfig(tenant_id=req.tenant_id),
        )
        response_parts: list[str] = []
        async for token in llm.stream_generate(ctx, messages):
            response_parts.append(token)
            yield token

        # 6. yield citations（流结束后推送检索来源）
        citations = self._build_citations(rag_result)
        if citations:
            yield citations

        # 7. 后台：保存 assistant 消息 + 保存 citations + 压缩 + 记忆提取
        asyncio.create_task(
            _extractor.run(
                session_id=req.session_id,
                tenant_id=req.tenant_id,
                user_id=req.user_id,
                space_id=req.space_id,
                assistant_response="".join(response_parts),
                citations=citations,
                llm_provider_name=req.llm_provider_name,
                embedding_provider_name=req.embedding_provider_name,
            )
        )

    # ── 原有简单流程（向后兼容，无 session）─────────────────────────────────

    async def _stream_simple(self, req: ChatRequest) -> AsyncIterator[str | list]:
        async for item in _orchestrator.chat(
            query=req.query,
            tenant_id=req.tenant_id,
            history=req.history,
            top_k=req.top_k,
            score_threshold=req.score_threshold,
            model=req.model,
        ):
            yield item
