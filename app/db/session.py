from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_recycle=settings.database_pool_recycle,
        echo=settings.database_echo,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_sessionmaker()() as db:
        yield db


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise


def reset_database_state() -> None:
    sessionmaker_cache_clear = getattr(get_sessionmaker, "cache_clear", None)
    engine_cache_clear = getattr(get_engine, "cache_clear", None)
    if callable(sessionmaker_cache_clear):
        sessionmaker_cache_clear()
    if callable(engine_cache_clear):
        engine_cache_clear()
