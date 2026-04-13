from __future__ import annotations

from app.domain.retrieval import SearchContext, SearchHit
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage

# RRF 平滑参数，通常取 60（经验值）
_RRF_K = 60


@registry.stage("rrf_fusion")
class RRFFusionStage(BaseStage[SearchContext, list[SearchHit]]):
    """
    Reciprocal Rank Fusion（RRF）融合 Stage。

    将向量检索和关键词检索的结果列表合并，
    按 RRF 分数重新排序后输出 top_k 条。

    RRF 公式：score(d) = Σ 1 / (k + rank(d, list_i))
    其中 rank 从 1 开始，k=60 为平滑参数。
    """

    name = "rrf_fusion"

    async def _execute(
        self,
        ctx: ProcessingContext,
        search_ctx: SearchContext,
    ) -> list[SearchHit]:
        top_k = search_ctx.query.top_k
        rrf_scores: dict[str, float] = {}
        hit_map: dict[str, SearchHit] = {}

        # 按分数降序排列后计算 RRF 排名
        for hits in (search_ctx.vector_hits, search_ctx.keyword_hits):
            ranked = sorted(hits, key=lambda h: h.score, reverse=True)
            for rank, hit in enumerate(ranked, start=1):
                rrf_scores[hit.chunk_id] = rrf_scores.get(hit.chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
                if hit.chunk_id not in hit_map:
                    hit_map[hit.chunk_id] = hit

        # 按 RRF 分数降序，取 top_k
        sorted_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)[:top_k]
        results: list[SearchHit] = []
        for rank, chunk_id in enumerate(sorted_ids, start=1):
            hit = hit_map[chunk_id]
            results.append(SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                chunk_index=hit.chunk_index,
                score=rrf_scores[chunk_id],
                source=hit.source,
                rank=rank,
            ))
        return results
