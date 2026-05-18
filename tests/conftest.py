import asyncio
import base64
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from webhook_inspector.web.app.main import app as web_app
from webhook_inspector.web.ingestor.main import app as ingestor_app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def postgres_container() -> AsyncIterator[PostgresContainer]:
    container = PostgresContainer("postgres:16", driver="psycopg")
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    # psycopg3 supports async natively via postgresql+psycopg:// — no driver rename needed
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
async def engine(database_url: str):
    """Build schema via alembic migrations — matches production exactly.

    Switched from `SQLModel.metadata.create_all` once migrations started
    introducing expression-based indexes and CHECK constraints that the
    ORM metadata can't represent (e.g. migration 0006's partial unique
    index on `COALESCE(event_type, '')`). Without migrations, ON CONFLICT
    upserts that target those indexes silently fail in tests.
    """
    eng = create_async_engine(database_url, future=True)
    # Run alembic upgrade head against this test database. migrations/env.py
    # internally calls asyncio.run() which collides with pytest-asyncio's
    # running loop, so dispatch to a worker thread that has its own loop.
    import asyncio

    from alembic import command
    from alembic.config import Config

    sync_url = database_url.replace("+psycopg_async", "+psycopg").replace(
        "postgresql+psycopg://", "postgresql://"
    )
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s
        await s.rollback()


# 32 bytes base64 — stable across tests so encryption/decryption stays
# round-trippable within the same suite. Real prod key is per-environment.
_TEST_SECRETS_KEY_B64 = base64.b64encode(b"\x00" * 32).decode()


def _set_app_env(monkeypatch, database_url):
    """Common env setup for both app_client and ingestor_client."""
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", _TEST_SECRETS_KEY_B64)


@pytest.fixture
async def pg_session(session):
    """Alias for repo/integration tests that don't read fluently with 'session'."""
    return session


@pytest.fixture
def settings(monkeypatch, database_url):
    """Plain Settings instance for tests reading config values."""
    _set_app_env(monkeypatch, database_url)
    from webhook_inspector.config import Settings

    return Settings()


def _clear_summary_processor_buffer():
    """No-op until PR5 introduces tracing._summary_processor."""
    try:
        from webhook_inspector.observability import tracing

        proc = getattr(tracing, "_summary_processor", None)
        if proc is not None:
            proc._buffer.clear()
    except (ImportError, AttributeError):
        pass


@pytest.fixture
async def _reset_summary_processor():
    """Clear the InMemoryRequestSpanProcessor singleton between tests. The
    processor will be introduced by PR5 ; for now this fixture is a no-op
    because tracing._summary_processor doesn't exist yet — defensive against
    AttributeError, treats missing attr as 'nothing to reset'.
    """
    _clear_summary_processor_buffer()
    yield
    _clear_summary_processor_buffer()


@pytest.fixture
async def app_client(monkeypatch, database_url, engine, _reset_summary_processor):
    """httpx client wired to the web FastAPI app. LifespanManager triggers
    the app's lifespan (configure_tracing/metrics/instrument_app). Clears
    the 5 lru_cache factories in web/app/deps.py.

    Note : web/app/deps.py has 5 lru_caches (get_settings, _engine,
    _session_factory, _meter, get_metrics). NO _blob_storage (that lives
    on the ingestor side). Don't add it.
    """
    _set_app_env(monkeypatch, database_url)
    from webhook_inspector.web.app import deps

    for fn in (
        deps.get_settings,
        deps._engine,
        deps._session_factory,
        deps._meter,
        deps.get_metrics,
    ):
        fn.cache_clear()
    async with LifespanManager(web_app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=web_app), base_url="http://test"
        ) as c:
            yield c


@pytest.fixture
async def ingestor_client(monkeypatch, database_url, engine, _reset_summary_processor):
    """Mirror of app_client for the ingestor service. Clears the 6 lru_caches
    in web/ingestor/deps.py (the extra one vs app is _blob_storage).
    """
    _set_app_env(monkeypatch, database_url)
    from webhook_inspector.web.ingestor import deps as ing_deps

    for fn in (
        ing_deps.get_settings,
        ing_deps._engine,
        ing_deps._session_factory,
        ing_deps._blob_storage,
        ing_deps._meter,
        ing_deps.get_metrics,
    ):
        fn.cache_clear()
    # Reset the schema queue singleton so each test starts with a clean state.
    ing_deps._schema_queue_singleton = None
    async with LifespanManager(ingestor_app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=ingestor_app), base_url="http://test"
        ) as c:
            yield c
