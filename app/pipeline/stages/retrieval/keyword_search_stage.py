from __future__ import annotations

from app.domain.retrieval import SearchContext, SearchHit
from app.infrastructure.elasticsearch.client import bm25_search
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage


@registry.stage("keyword_search")
class KeywordSearchStage(BaseStage[SearchContext, SearchContext]):
    """
    BM25 关键词检索 Stage。

    调用 Elasticsearch 全文检索，
    结果写入 SearchContext.keyword_hits。
    """

    name = "keyword_search"

    async def _execute(
        self,
        ctx: ProcessingContext,
        search_ctx: SearchContext,
    ) -> SearchContext:
        query = search_ctx.query
        raw_hits = await bm25_search(
            query_text=query.query_text,
            tenant_id=query.tenant_id,
            top_k=query.top_k,
        )
        keyword_hits = [
            SearchHit(
                chunk_id=h["chunk_id"],
                document_id=h["document_id"],
                chunk_index=h["chunk_index"],
                score=h["score"],
                source="keyword",
            )
            for h in raw_hits
        ]
        return SearchContext(
            query=search_ctx.query,
            vector_hits=search_ctx.vector_hits,
            keyword_hits=keyword_hits,
        )
