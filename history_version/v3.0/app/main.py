from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import chat, document, search
from app.config.settings import settings
from app.infrastructure.postgres.client import dispose


def _register_components() -> None:
    """
    导入所有 Stage / Provider / Strategy 模块，触发 @registry.xxx() 装饰器注册。
    必须在应用启动时执行，否则 registry.get_stage() 会找不到对应实现。
    """
    # ── Ingestion ────────────────────────────────────────────────────────────
    import app.pipeline.stages.chunking.token_chunker  # noqa: F401
    import app.pipeline.stages.embedding.embed_stage  # noqa: F401
    import app.pipeline.stages.embedding.es_index_stage  # noqa: F401
    import app.pipeline.stages.embedding.milvus_index_stage  # noqa: F401
    import app.pipeline.stages.parsing.parser_stage  # noqa: F401
    import app.pipeline.strategies.ingestion.ocr_strategy  # noqa: F401
    import app.pipeline.strategies.ingestion.standard_strategy  # noqa: F401
    import app.providers.embedding.openai_embedding  # noqa: F401
    import app.providers.parser.paddleocr_provider  # noqa: F401
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 启动：注册所有组件
    _register_components()
    yield
    # 关闭：释放连接池
    await dispose()


app = FastAPI(
    title="ArcKnowledge AI Service",
    description="文档处理、向量检索、RAG 生成",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(document.router)
app.include_router(search.router)
app.include_router(chat.router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env}
