from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import struct
import time
import uuid
from dataclasses import dataclass

from prometheus_client import Counter, Histogram

from app.config.settings import settings
from app.infrastructure.redis.client import get_redis_binary
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.providers.base import EmbeddingProvider

logger = logging.getLogger(__name__)

_cache_hits = Counter(
    "semantic_cache_hits_total",
    "Semantic cache hits by tenant and hit type",
    ["tenant_id", "hit_type"],
)
_cache_misses = Counter(
    "semantic_cache_misses_total",
    "Semantic cache misses by tenant",
    ["tenant_id"],
)
_cache_score = Histogram(
    "semantic_cache_score",
    "Cosine similarity score on semantic cache hit",
    ["tenant_id"],
    buckets=[0.85, 0.88, 0.90, 0.92, 0.95, 0.98, 1.0],
)

_FAKE_QUOTA = QuotaSnapshot(
    max_documents=10000,
    max_storage_bytes=10 * 1024**3,
    max_api_calls_per_day=100000,
    used_documents=0,
    used_storage_bytes=0,
    used_api_calls_today=0,
)
# embed() 需要 ProcessingContext，但 OpenAI Embedding 实现并不使用它
_CACHE_CTX = ProcessingContext.create(
    tenant_id="__system__",
    document_id="",
    quota=_FAKE_QUOTA,
    config=TenantConfig(tenant_id="__system__"),
)

# 本进程内已创建索引的租户，避免每次请求都调用 FT.CREATE
_indexed_tenants: set[str] = set()


@dataclass
class CacheEntry:
    answer: str
    citations: list[dict]
    hit_type: str   # "exact" | "semantic"
    score: float = 1.0


def _normalize(query: str) -> str:
    query = query.strip()
    query = re.sub(r'\s+', ' ', query) #匹配内部多个空白字符（空格、Tab、换行 \n、回车 \r）替换为 单个空白字符
    query = query.rstrip('？?。.')
    query = query.lower()
    return query


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _to_bytes(vec: list[float]) -> bytes:
    # Redis Stack HNSW 要求 FLOAT32 二进制格式；Python float 是 64 位，'f' 格式字符转为 32 位
    return struct.pack(f'{len(vec)}f', *vec)


class SemanticCache:
    def __init__(self, embedding_provider: EmbeddingProvider | None = None) -> None:
        self._explicit_provider = embedding_provider
        self._provider_name = settings.semantic_cache_embedding_provider
        self._threshold = settings.semantic_cache_threshold
        self._ttl = settings.semantic_cache_ttl_seconds
        self._enabled = settings.semantic_cache_enabled

    def _get_provider(self) -> EmbeddingProvider:
        if self._explicit_provider is not None:
            return self._explicit_provider
        # 延迟 import 避免循环依赖：registry 在 main.py 启动时才完成注册
        from app.pipeline.core.registry import registry
        return registry.get_provider(self._provider_name)

    async def initialize(self) -> None:
        logger.info(
            "SemanticCache ready (enabled=%s, threshold=%.2f, ttl=%ds)",
            self._enabled, self._threshold, self._ttl,
        )

    async def _ensure_index(self, tenant_id: str, space_id: str = "default") -> None:
        """为租户+空间创建 HNSW 向量索引（幂等，进程内只创建一次）。"""
        cache_key = f"{tenant_id}:{space_id}"
        if cache_key in _indexed_tenants:
            return

        dim = self._get_provider().get_dimension()
        redis = get_redis_binary()
        index_name = f"idx:cache:{tenant_id}:{space_id}"
        try:
            await redis.execute_command(
                "FT.CREATE", index_name,
                "ON", "HASH",
                "PREFIX", "1", f"cache:vec:{tenant_id}:{space_id}:",
                "SCHEMA",
                "vector", "VECTOR", "HNSW", "10",
                "TYPE", "FLOAT32",
                "DIM", str(dim),
                "DISTANCE_METRIC", "COSINE",
                "M", "16",                  # HNSW 每节点最大边数，Redis Stack 推荐知识库场景默认值
                "EF_CONSTRUCTION", "200",   # 构建时搜索宽度，值越大索引质量越高但写入越慢
                "created_at", "NUMERIC",
            )
            logger.info("Created cache index for tenant %s (dim=%d)", tenant_id, dim)
        except Exception as e:
            # 并发首请求可能同时触发 FT.CREATE，后到的会报 "Index already exists"，属于正常竞态，忽略即可
            if "Index already exists" not in str(e):
                raise
        _indexed_tenants.add(cache_key)

    async def get(self, query: str, tenant_id: str, space_id: str = "default") -> CacheEntry | None:
        if not self._enabled:
            return None

        normalized = _normalize(query)
        redis = get_redis_binary()

        # 第一层：精确匹配（O(1)，零 Embedding 消耗）
        exact_key = f"cache:exact:{tenant_id}:{space_id}:{_md5(normalized)}"
        raw = await redis.get(exact_key)
        if raw:
            try:
                data = json.loads(raw)
                _cache_hits.labels(tenant_id=tenant_id, hit_type="exact").inc()
                return CacheEntry(
                    answer=data["answer"],
                    citations=data.get("citations", []),
                    hit_type="exact",
                )
            except Exception as e:
                logger.warning("Exact cache parse error: %s", e)

        # 第二层：语义向量匹配
        try:
            await self._ensure_index(tenant_id, space_id)
            provider = self._get_provider()
            embeddings = await provider.embed(_CACHE_CTX, [normalized])
            vec_bytes = _to_bytes(embeddings[0])

            result = await redis.execute_command(
                "FT.SEARCH", f"idx:cache:{tenant_id}:{space_id}",
                "*=>[KNN 1 @vector $BLOB AS score]",
                "PARAMS", "2", "BLOB", vec_bytes,
                "RETURN", "3", "answer", "citations", "score",
                "SORTBY", "score",
                "DIALECT", "2",
            )

            if result and result[0] > 0:
                fields_raw = result[2]
                fields: dict[str, str] = {}
                for i in range(0, len(fields_raw), 2):
                    k = fields_raw[i]
                    v = fields_raw[i + 1]
                    fields[k.decode() if isinstance(k, bytes) else k] = (
                        v.decode() if isinstance(v, bytes) else str(v)
                    )

                # Redis Stack COSINE 返回的是距离（distance = 1 - cosine_similarity）
                # 默认值 2.0 = 最大距离（完全相反的向量），确保无结果时不误判为命中
                distance = float(fields.get("score", 2.0))
                similarity = 1.0 - distance

                if similarity >= self._threshold:
                    _cache_hits.labels(tenant_id=tenant_id, hit_type="semantic").inc()
                    _cache_score.labels(tenant_id=tenant_id).observe(similarity)
                    return CacheEntry(
                        answer=fields["answer"],
                        citations=json.loads(fields.get("citations", "[]")),
                        hit_type="semantic",
                        score=similarity,
                    )

        except Exception as e:
            logger.warning("Semantic cache get error: %s", e)

        _cache_misses.labels(tenant_id=tenant_id).inc()
        return None

    async def set(
        self,
        query: str,
        answer: str,
        citations: list[dict],
        tenant_id: str,
        space_id: str = "default",
    ) -> None:
        if not self._enabled:
            return

        normalized = _normalize(query)
        # ±300s 随机抖动，防止同一批写入的 key 集中过期引发缓存雪崩
        ttl = self._ttl + random.randint(-300, 300)

        try:
            redis = get_redis_binary()

            # 写精确匹配缓存
            exact_key = f"cache:exact:{tenant_id}:{space_id}:{_md5(normalized)}"
            await redis.set(
                exact_key,
                json.dumps({"answer": answer, "citations": citations}),
                ex=ttl,
            )

            # 写向量缓存
            await self._ensure_index(tenant_id, space_id)
            provider = self._get_provider()
            embeddings = await provider.embed(_CACHE_CTX, [normalized])
            vec_bytes = _to_bytes(embeddings[0])

            # 每条记录用独立 uuid key，避免不同问题的向量互相覆盖，保持 HNSW 索引多样性
            vec_key = f"cache:vec:{tenant_id}:{space_id}:{uuid.uuid4()}"
            await redis.hset(vec_key, mapping={
                b"vector":     vec_bytes,
                b"answer":     answer.encode(),
                b"citations":  json.dumps(citations).encode(),
                b"created_at": str(int(time.time())).encode(),
            })
            await redis.expire(vec_key, ttl)

        except Exception as e:
            logger.warning("Semantic cache set error: %s", e)

    async def invalidate(self, tenant_id: str) -> None:
        """删除文档或重新上传时，清除该租户的全部语义缓存。"""
        _indexed_tenants.discard(tenant_id)
        redis = get_redis_binary()

        # 删除向量索引（同时移除 Redis Stack 中的索引条目）
        try:
            await redis.execute_command("FT.DROPINDEX", f"idx:cache:{tenant_id}")
        except Exception:
            pass

        # 删除精确匹配 key
        cursor = 0
        while True:
            cursor, keys = await redis.scan(
                cursor, match=f"cache:exact:{tenant_id}:*", count=100
            )
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break

        logger.info("Invalidated semantic cache for tenant %s", tenant_id)


# 全局单例，由 rag_orchestrator 和 document_service 共享
cache = SemanticCache()
