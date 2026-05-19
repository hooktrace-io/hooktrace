"""Unit tests for the prod fail-fast assertions in each app's lifespan.

The prod lifespan refuses to serve traffic when REDIS_URL (web/ingestor +
worker) or RATE_LIMIT_REDIS_URL (web/ingestor only) is unset. Catches
misconfigured Fly secrets at deploy time instead of silently degrading
to NullForwardQueue / fail-open rate limit middleware.

Local/dev/test bypasses the check by leaving environment != "prod".
"""

import asyncio
import base64

import pytest
from asgi_lifespan import LifespanManager

_TEST_SECRETS_KEY_B64 = base64.b64encode(b"\x00" * 32).decode()


def _common_prod_env(monkeypatch, database_url: str) -> None:
    """Set the env shared by every prod-lifespan assertion test."""
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", _TEST_SECRETS_KEY_B64)
    monkeypatch.setenv("ENVIRONMENT", "prod")


def _clear_caches(*modules) -> None:  # type: ignore[no-untyped-def]
    for m in modules:
        for fn in (
            getattr(m, "get_settings", None),
            getattr(m, "_engine", None),
            getattr(m, "_session_factory", None),
            getattr(m, "_blob_storage", None),
            getattr(m, "_meter", None),
            getattr(m, "get_metrics", None),
        ):
            if fn is not None and hasattr(fn, "cache_clear"):
                fn.cache_clear()


@pytest.mark.asyncio
async def test_web_lifespan_fails_when_redis_url_missing_in_prod(monkeypatch, database_url, engine):
    """ENVIRONMENT=prod with REDIS_URL unset must raise RuntimeError at
    lifespan startup so Fly aborts the rollout instead of serving with a
    silently-disabled forward queue.
    """
    _common_prod_env(monkeypatch, database_url)
    monkeypatch.delenv("REDIS_URL", raising=False)
    # RATE_LIMIT_REDIS_URL is checked AFTER REDIS_URL, so leaving it
    # unset would also raise — set it so we're sure we're catching the
    # REDIS_URL branch specifically.
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "redis://localhost:6379")

    from webhook_inspector.web.app import deps
    from webhook_inspector.web.app.main import app as web_app

    _clear_caches(deps)
    deps._forward_queue_singleton = None

    with pytest.raises(RuntimeError, match="REDIS_URL is required"):
        async with LifespanManager(web_app):
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_web_lifespan_fails_when_rate_limit_redis_url_missing_in_prod(
    monkeypatch, database_url, engine
):
    _common_prod_env(monkeypatch, database_url)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)

    from webhook_inspector.web.app import deps
    from webhook_inspector.web.app.main import app as web_app

    _clear_caches(deps)
    deps._forward_queue_singleton = None

    with pytest.raises(RuntimeError, match="RATE_LIMIT_REDIS_URL is required"):
        async with LifespanManager(web_app):
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_ingestor_lifespan_fails_when_redis_url_missing_in_prod(
    monkeypatch, database_url, engine, tmp_path
):
    _common_prod_env(monkeypatch, database_url)
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "redis://localhost:6379")

    from webhook_inspector.web.ingestor import deps
    from webhook_inspector.web.ingestor.main import app as ingestor_app

    _clear_caches(deps)
    deps._forward_queue_singleton = None

    with pytest.raises(RuntimeError, match="REDIS_URL is required"):
        async with LifespanManager(ingestor_app):
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_ingestor_lifespan_fails_when_rate_limit_redis_url_missing_in_prod(
    monkeypatch, database_url, engine, tmp_path
):
    _common_prod_env(monkeypatch, database_url)
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)

    from webhook_inspector.web.ingestor import deps
    from webhook_inspector.web.ingestor.main import app as ingestor_app

    _clear_caches(deps)
    deps._forward_queue_singleton = None

    with pytest.raises(RuntimeError, match="RATE_LIMIT_REDIS_URL is required"):
        async with LifespanManager(ingestor_app):
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_worker_startup_fails_when_redis_url_missing_in_prod(monkeypatch, database_url):
    """WorkerSettings.on_startup raises so arq aborts before draining a
    silently-disconnected localhost queue.
    """
    _common_prod_env(monkeypatch, database_url)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from webhook_inspector.jobs.worker import startup

    ctx: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="REDIS_URL is required"):
        await startup(ctx)


@pytest.mark.asyncio
async def test_worker_startup_does_not_check_rate_limit_redis_url(monkeypatch, database_url):
    """Worker doesn't read RATE_LIMIT_REDIS_URL — its absence in prod must
    NOT raise. (Asserts the worker check is intentionally narrower than
    web/ingestor's two-key check.)
    """
    _common_prod_env(monkeypatch, database_url)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)

    from webhook_inspector.jobs.worker import shutdown, startup

    ctx: dict[str, object] = {}
    await startup(ctx)
    # Must have stashed the engine + session factory etc. — startup ran.
    assert "_engine" in ctx
    assert "_session_factory" in ctx
    # Don't leak the engine across tests.
    await shutdown(ctx)
