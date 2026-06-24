from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.dependencies import UserContext, require_user
from app.api.filter_params import parse_metadata_filters
from app.services.chat_service import ChatRequest, ChatService

router = APIRouter(prefix="/chat", tags=["rag"])
_service = ChatService()


class ChatRequestBody(BaseModel):
    query: str
    space_id: str
    session_id: str | None = None  # 提供时启用三层记忆
    history: list[dict] = []
    top_k: int = 10
    score_threshold: float = 0.5
    model: str | None = None  # 请求级模型覆盖，优先于租户配置
    metadata_filters: list[dict] = []  # 通用元数据过滤条件 [{key, operator, value}]


async def _sse_stream(event_iter: AsyncIterator) -> AsyncIterator[bytes]:
    """将 token / citations 流包装为 SSE 格式"""
    try:
        async for item in event_iter:
            if isinstance(item, list):
                payload = json.dumps({"citations": item}, ensure_ascii=False)
            else:
                payload = json.dumps({"delta": item}, ensure_ascii=False)
            yield f"data: {payload}\n\n".encode()
    except Exception as exc:
        error_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        yield f"data: {error_payload}\n\n".encode()
    finally:
        yield b"data: [DONE]\n\n"


@router.post("")
async def chat(
    body: ChatRequestBody,
    user: UserContext = Depends(require_user),
) -> StreamingResponse:
    """
    RAG 问答接口（SSE 流式输出）。

    - 携带 session_id → 启用三层记忆（工作记忆 + 语义记忆 + RAG）
    - 不携带 session_id → 原有无状态模式（history 字段 + RAG）

    SSE 格式：
      data: {"delta": "token1"}
      data: [DONE]
    """
    req = ChatRequest(
        query=body.query,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        space_id=body.space_id,
        session_id=body.session_id,
        history=body.history,
        top_k=body.top_k,
        score_threshold=body.score_threshold,
        model=body.model,
        metadata_filters=parse_metadata_filters(body.metadata_filters),
        embedding_provider_name="openai_embedding",
    )
    token_stream = _service.stream_chat(req)
    return StreamingResponse(
        _sse_stream(token_stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
