from __future__ import annotations

import logging

from app.domain.retrieval import SearchContext, SearchHit
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage
from app.providers.base import RerankProvider

logger = logging.getLogger(__name__)


@registry.stage("rerank")
class RerankStage(BaseStage[SearchContext, list[SearchHit]]):
    """
    Rerank Stage：对 RRF 融合后的候选集做精排。

    输入：SearchContext（含 ranked_hits 和 query.query_text）
    输出：list[SearchHit]（按 rerank 分数重排后的最终结果）

    流程：
    1. 从 PostgreSQL 拉取 ranked_hits 对应的 chunk 文本
    2. 调用 RerankProvider（默认 BGE-Reranker）对 (query, chunk) 打分
    3. 按 rerank 分数降序重排，返回 top_k 条

    rerank_enabled=False 或 RerankProvider 不可用时，直接返回 ranked_hits（RRF 顺序）。
    任何运行时错误均降级为 RRF 顺序，不中断请求。
    """

    name = "rerank"

    def __init__(self, provider: RerankProvider | None = None) -> None:
        self._provider = provider

    def _get_provider(self, ctx: ProcessingContext) -> RerankProvider | None:
        if self._provider is not None:
            return self._provider
        try:
            return registry.get_provider(ctx.config.rerank_provider)  # type: ignore[return-value]
        except Exception:
            return None

    async def _execute(
        self,
        ctx: ProcessingContext,
        search_ctx: SearchContext,
    ) -> list[SearchHit]:
        hits = search_ctx.ranked_hits

        if not ctx.config.rerank_enabled or not hits:
            return hits

        provider = self._get_provider(ctx)
        if provider is None:
            logger.warning("RerankStage: provider %r not found, skipping rerank", ctx.config.rerank_provider)
            return hits

        try:
            from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
            chunk_repo = ChunkRepository()
            chunks = await chunk_repo.get_chunks_by_ids(
                [h.chunk_id for h in hits],
                ctx.tenant_id,
            )
            content_map: dict[str, str] = {c["chunk_id"]: c["content"] for c in chunks}

            documents = [content_map.get(h.chunk_id, "") for h in hits]
            # 过滤掉文本为空的 hit（内容未拉取到），但保留其位置信息用于最终拼回
            valid_indices = [i for i, doc in enumerate(documents) if doc]
            if not valid_indices:
                return hits

            valid_docs = [documents[i] for i in valid_indices]
            ranked = await provider.rerank(
                ctx,
                query=search_ctx.query.query_text,
                documents=valid_docs,
                top_n=search_ctx.query.top_k,
            )
            # ranked: [(valid_docs 内的索引, score)]，按 score 降序
            results: list[SearchHit] = []
            for new_rank, (doc_idx, score) in enumerate(ranked, start=1):
                original_idx = valid_indices[doc_idx]
                hit = hits[original_idx]
                results.append(SearchHit(
                    chunk_id=hit.chunk_id,
                    document_id=hit.document_id,
                    chunk_index=hit.chunk_index,
                    score=score,
                    source=hit.source,
                    rank=new_rank,
                ))
            return results

        except Exception:
            logger.warning(
                "RerankStage failed for query=%r, falling back to RRF order",
                search_ctx.query.query_text,
                exc_info=True,
            )
            return hits
