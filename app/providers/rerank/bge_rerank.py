from __future__ import annotations

import asyncio
import logging
from functools import partial

from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.providers.base import HealthStatus, RerankProvider

logger = logging.getLogger(__name__)

_MODEL_NAME = "BAAI/bge-reranker-v2-m3"


@registry.provider("bge_rerank")
class BGERerankerProvider(RerankProvider):
    """
    BGE-Reranker-v2-m3 本地精排 Provider。

    使用 FlagEmbedding 库的 FlagReranker，在 CPU/GPU 上运行 cross-encoder 推理。
    模型在首次调用时懒加载，后续复用同一实例。
    推理在线程池中执行（run_in_executor），避免阻塞 asyncio 事件循环。
    """

    provider_id = "bge_rerank"

    def __init__(self) -> None:
        self._reranker = None  # 懒加载

    def _load_reranker(self):
        if self._reranker is None:
            try:
                from FlagEmbedding import FlagReranker  # type: ignore[import]
                self._reranker = FlagReranker(_MODEL_NAME, use_fp16=True)  #加载cross embedding模型 fp16是浮点数表示，用fp16可以加速
                logger.info("BGERerankerProvider: model %s loaded", _MODEL_NAME)
            except ImportError as e:
                raise RuntimeError(
                    "FlagEmbedding 未安装，请执行: pip install FlagEmbedding"
                ) from e
        return self._reranker

    async def health_check(self) -> HealthStatus:
        try:
            self._load_reranker()
            return HealthStatus.HEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY

    async def rerank(
        self,
        ctx: ProcessingContext,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        """
        返回 [(原始索引, score)]，按相关性降序，最多 top_n 条。

        score 由 BGE cross-encoder 给出，值域无固定范围（sigmoid 前的 logit），
        越大表示 query 与 document 越相关。
        """
        if not documents:
            return []

        reranker = self._load_reranker()
        #构建输入对
        pairs = [[query, doc] for doc in documents]
        #异步执行推理，获取事件循环，执行线程调用
        #partial 执行函数打包
        loop = asyncio.get_event_loop()
        scores: list[float] = await loop.run_in_executor(
            None,
            partial(reranker.compute_score, pairs, normalize=True),
        )

        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(idx, score) for idx, score in indexed[:top_n]]
