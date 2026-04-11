from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator

from app.providers.base import ChatMessage
from app.workflows.rag_orchestrator import RAGOrchestrator

_orchestrator = RAGOrchestrator()


@dataclass
class ChatRequest:
    query: str
    tenant_id: str
    space_id: str
    history: list[dict] = field(default_factory=list)  # [{"role": ..., "content": ...}]
    top_k: int = 10
    score_threshold: float = 0.5


class ChatService:
    """
    RAG 问答服务。

    流程：
    1. 混合检索（RetrievalPipeline）
    2. LLM 流式生成（SSE）
    """

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[str]:
        # 1. 检索
        result = await _orchestrator.retrieve(
            query_text=req.query,
            tenant_id=req.tenant_id,
            top_k=req.top_k,
            score_threshold=req.score_threshold,
        )

        # 2. 构建历史消息
        history = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in req.history
        ]

        # 3. 流式生成
        async for token in _orchestrator.stream_generate(
            result=result,
            history=history,
            tenant_id=req.tenant_id,
        ):
            yield token
