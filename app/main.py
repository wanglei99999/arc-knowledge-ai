from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.routers import admin, auth, chat, chat_turn, document, ops, search, session, spaces
from app.config.settings import settings
from app.infrastructure.postgres.client import dispose
from app.infrastructure.redis.client import close as redis_close
from app.infrastructure.redis.semantic_cache import cache as _semantic_cache
from app.infrastructure.telemetry.otel import setup_tracing
from app.services.usage_service import register_handlers as _register_usage_handlers


def _register_components() -> None:
    """
    导入所有 Stage / Provider / Strategy 模块，触发 @registry.xxx() 装饰器注册。
    必须在应用启动时执行，否则 registry.get_stage() 会找不到对应实现。
    """
    # ── Ingestion ────────────────────────────────────────────────────────────
    import app.pipeline.stages.chunking.token_chunker  # noqa: F401
    import app.pipeline.stages.chunking.markdown_chunker  # noqa: F401
    import app.pipeline.stages.embedding.embed_stage  # noqa: F401
    import app.pipeline.stages.embedding.es_index_stage  # noqa: F401
    import app.pipeline.stages.embedding.milvus_index_stage  # noqa: F401
    import app.pipeline.stages.parsing.parser_stage  # noqa: F401
    import app.pipeline.strategies.ingestion.ocr_strategy  # noqa: F401
    import app.pipeline.strategies.ingestion.standard_strategy  # noqa: F401
    import app.providers.embedding.openai_embedding  # noqa: F401
    import app.providers.parser.mineru_provider  # noqa: F401
    import app.providers.parser.paddleocr_provider  # noqa: F401
    import app.providers.parser.smart_parser_provider  # noqa: F401
    import app.providers.parser.unstructured_provider  # noqa: F401

    # ── Retrieval / RAG ──────────────────────────────────────────────────────
    import app.pipeline.stages.retrieval.keyword_search_stage  # noqa: F401
    import app.pipeline.stages.retrieval.query_rewrite_stage  # noqa: F401
    import app.pipeline.stages.retrieval.rerank_stage  # noqa: F401
    import app.pipeline.stages.retrieval.rrf_fusion_stage  # noqa: F401
    import app.pipeline.stages.retrieval.vector_search_stage  # noqa: F401
    import app.pipeline.strategies.retrieval.hybrid_strategy  # noqa: F401
    import app.providers.llm.ollama_llm  # noqa: F401
    import app.providers.llm.openai_llm  # noqa: F401
    import app.providers.rerank.bge_rerank  # noqa: F401
    import app.providers.rerank.infinity_rerank  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Settings 已在模块导入时初始化完毕，此处设置不会被 Pydantic 校验。
    # 总是注入 HF_ENDPOINT（镜像站），reranker_offline=True 时再锁定离线模式。
    os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
    if settings.reranker_offline:
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    # 启动：初始化 OTel tracing
    if settings.otel_enabled:
        setup_tracing(settings.otel_service_name, settings.otlp_endpoint)
    # 启动：注册所有组件（必须在 semantic_cache.initialize 之前，registry 需要先就绪）
    _register_components()
    _register_usage_handlers()
    await _semantic_cache.initialize()
    yield
    # 关闭：释放连接池
    await dispose()
    await redis_close()


app = FastAPI(
    title="ArcKnowledge AI Service",
    description="文档处理、向量检索、RAG 生成",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env != "production" else None,
)

app.add_middleware(RateLimitMiddleware)  # 内层：CORS 之后执行
app.add_middleware(
    CORSMiddleware,  # 外层：最先执行，处理 OPTIONS 预检
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP 根 span：每个请求一个 SERVER span（如 "POST /chat"），
# 管线/LLM 的子 span 自动挂其下。span 在请求时才产生，此处注册不依赖 provider 就绪。
if settings.otel_enabled:
    FastAPIInstrumentor.instrument_app(app)

# 注册路由
app.include_router(auth.router)
app.include_router(document.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(chat_turn.router)
app.include_router(admin.router)
app.include_router(session.router)
app.include_router(spaces.router)
app.include_router(ops.router)


@app.get("/metrics", tags=["ops"], include_in_schema=False)
async def metrics() -> PlainTextResponse:
    """Prometheus metrics 端点，供 Prometheus scrape。"""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
