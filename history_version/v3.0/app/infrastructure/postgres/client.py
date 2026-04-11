from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings

# 连接池：最大 20 个连接，溢出 10 个
_engine = create_async_engine(
    settings.postgres_url,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,   # 心跳检测，避免使用已断开的连接
    echo=settings.app_env == "development",
)

_SessionFactory = async_sessionmaker(
    _engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """
    异步数据库 session 上下文管理器。

    用法：
        async with get_session() as session:
            await session.execute(...)
    """
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose() -> None:
    """应用关闭时释放连接池"""
    await _engine.dispose()
