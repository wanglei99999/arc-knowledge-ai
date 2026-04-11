from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.dependencies import require_tenant
from app.services.chat_service import ChatRequest, ChatService

router = APIRouter(prefix="/chat", tags=["rag"])
_service = ChatService()


class ChatRequestBody(BaseModel):
    query: str
    space_id: str
    history: list[dict] = []
    top_k: int = 10
    score_threshold: float = 0.5


async def _sse_stream(token_iter: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """将 token 流包装为 SSE 格式"""
    async for token in token_iter:
        payload = json.dumps({"delta": token}, ensure_ascii=False)
        yield f"data: {payload}\n\n".encode()
    yield b"data: [DONE]\n\n"


@router.post("")
async def chat(
    body: ChatRequestBody,
    tenant_id: str = Depends(require_tenant),
) -> StreamingResponse:
    """
    RAG 问答接口（SSE 流式输出）。

    流程：混合检索 → 构建 Prompt → LLM 流式生成。

    SSE 格式：
      data: {"delta": "token1"}
      data: {"delta": "token2"}
      data: [DONE]
    """
    req = ChatRequest(
        query=body.query,
        tenant_id=tenant_id,
        space_id=body.space_id,
        history=body.history,
        top_k=body.top_k,
        score_threshold=body.score_threshold,
    )
    token_stream = _service.stream_chat(req)
    return StreamingResponse(
        _sse_stream(token_stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
