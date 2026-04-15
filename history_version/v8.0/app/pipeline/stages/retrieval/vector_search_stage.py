from __future__ import annotations

from app.domain.retrieval import SearchContext, SearchHit
from app.infrastructure.milvus.client import search_vectors
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage
from app.providers.base import EmbeddingProvider


@registry.stage("vector_search")
class VectorSearchStage(BaseStage[SearchContext, SearchContext]):
    """
    向量检索 Stage。

    将查询文本向量化后调用 Milvus ANN 检索，
    结果写入 SearchContext.vector_hits。
    支持多查询（expanded_queries）：取各查询命中结果的并集，相同 chunk_id 取最高分。
    """

    name = "vector_search"

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self._provider = provider

    def _get_provider(self, ctx: ProcessingContext) -> EmbeddingProvider:
        if self._provider is not None:
            return self._provider
        from app.pipeline.core.registry import registry as _reg
        return _reg.get_provider(ctx.config.embedding_provider)  # type: ignore[return-value]

    async def _execute(
        self,
        ctx: ProcessingContext,
        search_ctx: SearchContext,
    ) -> SearchContext:
        query = search_ctx.query
        provider = self._get_provider(ctx)
        queries = query.expanded_queries if query.expanded_queries else [query.query_text]

        best_hits: dict[str, SearchHit] = {}
        for q_text in queries:
            vectors = await provider.embed(ctx, [q_text])
            raw_hits = await search_vectors(
                query_vector=vectors[0],
                tenant_id=query.tenant_id,
                top_k=query.top_k,
                score_threshold=query.score_threshold,
            )
            for h in raw_hits:
                hit = SearchHit(
                    chunk_id=h["chunk_id"],
                    document_id=h["document_id"],
                    chunk_index=h["chunk_index"],
                    score=h["score"],
                    source="vector",
                )
                # 同一 chunk 多次命中时保留最高分
                if hit.chunk_id not in best_hits or hit.score > best_hits[hit.chunk_id].score:
                    best_hits[hit.chunk_id] = hit

        return SearchContext(
            query=search_ctx.query,
            vector_hits=list(best_hits.values()),
            keyword_hits=search_ctx.keyword_hits,
        )
