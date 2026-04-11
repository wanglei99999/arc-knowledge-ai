from __future__ import annotations

from app.domain.retrieval import SearchContext
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage


@registry.stage("query_rewrite")
class QueryRewriteStage(BaseStage[SearchContext, SearchContext]):
    """
    查询改写（当前为 pass-through）。

    Phase 3 可接入 LLM 做 HyDE（假设文档扩展）或多查询扩展，
    将扩展后的查询列表填入 SearchContext.query.expanded_queries，
    供后续 VectorSearchStage 并发召回。
    """

    name = "query_rewrite"

    async def _execute(
        self,
        ctx: ProcessingContext,
        search_ctx: SearchContext,
    ) -> SearchContext:
        # Phase 2 直接透传；expanded_queries 为空时，各 SearchStage 使用原始 query_text
        return search_ctx
