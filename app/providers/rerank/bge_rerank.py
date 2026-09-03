from __future__ import annotations

import asyncio
import logging
import threading
from functools import partial

from app.config.settings import settings
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
    模型加载和推理均在线程池中执行（run_in_executor），避免阻塞 asyncio 事件循环。
    """

    provider_id = "bge_rerank"

    def __init__(self) -> None:
        self._reranker = None
        self._lock = threading.Lock()
        self._unavailable = False  # 首次加载失败后置 True，后续快速抛出不再重试

    def _load_reranker(self):
        if self._reranker is None:
            with self._lock:
                if self._reranker is None:
                    try:
                        from FlagEmbedding import FlagReranker  # type: ignore[import]

                        model = settings.reranker_model_path or _MODEL_NAME
                        self._reranker = FlagReranker(model, use_fp16=True)
                        logger.info("BGERerankerProvider: model loaded from %s", model)
                    except ImportError as e:
                        raise RuntimeError(
                            "FlagEmbedding 未安装，请执行: pip install FlagEmbedding"
                        ) from e
                    except Exception as e:
                        self._unavailable = True
                        logger.warning(
                            "BGERerankerProvider: model %s not found locally, rerank will be "
                            "skipped. Run `python scripts/download_bge_model.py` to download. "
                            "Reason: %s",
                            _MODEL_NAME,
                            e,
                        )
                        raise
        return self._reranker

    async def health_check(self) -> HealthStatus:
        if self._unavailable:
            return HealthStatus.UNHEALTHY  # 快速路径，无额外开销
        try:
            await asyncio.get_event_loop().run_in_executor(None, self._load_reranker)
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

        loop = asyncio.get_event_loop()
        # 模型加载放进线程池，防止 HuggingFace 联网重试阻塞事件循环
        reranker = await loop.run_in_executor(None, self._load_reranker)
        pairs = [[query, doc] for doc in documents]
        scores: list[float] = await loop.run_in_executor(
            None,
            partial(reranker.compute_score, pairs, normalize=True),
        )

        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(idx, score) for idx, score in indexed[:top_n]]
