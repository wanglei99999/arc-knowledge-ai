from __future__ import annotations

import redis.asyncio as aioredis

from app.config.settings import settings

_pool: aioredis.ConnectionPool | None = None
_binary_pool: aioredis.ConnectionPool | None = None


def _get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _pool


def _get_binary_pool() -> aioredis.ConnectionPool:
    global _binary_pool
    if _binary_pool is None:
        _binary_pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=False,
        )
    return _binary_pool


def get_redis() -> aioredis.Redis:
    """返回共享连接池的 Redis 客户端（decode_responses=True，用于字符串操作）。"""
    return aioredis.Redis(connection_pool=_get_pool())


def get_redis_binary() -> aioredis.Redis:
    """返回二进制连接池的 Redis 客户端（decode_responses=False，用于向量存储）。"""
    return aioredis.Redis(connection_pool=_get_binary_pool())


async def close() -> None:
    """应用关闭时释放所有连接池。"""
    global _pool, _binary_pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
    if _binary_pool is not None:
        await _binary_pool.disconnect()
        _binary_pool = None
