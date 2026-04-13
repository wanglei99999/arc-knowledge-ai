from __future__ import annotations

import redis.asyncio as aioredis

from app.config.settings import settings

_pool: aioredis.ConnectionPool | None = None


def _get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    """返回共享连接池的 Redis 客户端（每次调用返回同一池上的新连接句柄）。"""
    return aioredis.Redis(connection_pool=_get_pool())


async def close() -> None:
    """应用关闭时释放连接池。"""
    global _pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
