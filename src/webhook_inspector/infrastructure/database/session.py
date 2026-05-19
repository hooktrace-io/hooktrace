from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from webhook_inspector.config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    # Pool sizing: 10 + 20 overflow = 30 connections max per process.
    # With 3 apps (web/ingestor/worker) x up to 2 Fly machines each = 6 processes,
    # the cluster can request up to 180 connections. Default Fly Postgres
    # `shared-cpu-1x` allows ~50 max_connections — risk of saturation under
    # burst until the PG upgrade to `shared-cpu-2x` lifts the cap to ~200.
    #
    # `pool_timeout=10` fails fast instead of hanging 30s default — under
    # saturation the caller gets a TimeoutError they can surface, rather
    # than holding the request open.
    #
    # `pool_recycle=300` recycles connections every 5 min. Fly Postgres
    # occasionally drops idle conns; pool_pre_ping catches most, recycle is
    # a safety net.
    return create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        pool_timeout=10,
        pool_recycle=300,
        pool_pre_ping=True,
        future=True,
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
