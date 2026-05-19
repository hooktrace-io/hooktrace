"""Integration test for the IP-keyed rate-limit middleware against a
real Redis container.

The middleware reads ``RATE_LIMIT_REDIS_URL`` lazily at first request,
so we can set the env var inside the test and the existing app instance
will pick it up. We only test the ingestor (fail-closed) since that's
the abuse-facing surface and the Lua script + key shape is identical to
the web /api/ path.

Caveat: ``RateLimitMiddleware`` caches its Redis client + URL on the
middleware instance, which lives on the module-level FastAPI app. So
we MUST reset that cache before the test (so the env var is re-read)
AND after (so subsequent tests that share the same app don't keep
talking to a torn-down Redis container).
"""

import httpx
import pytest
from httpx import ASGITransport
from testcontainers.redis import RedisContainer

from webhook_inspector.web.ingestor.main import app as ingestor_service
from webhook_inspector.web.middleware import token_rate_limit
from webhook_inspector.web.middleware.rate_limit import RateLimitMiddleware


@pytest.fixture(scope="module")
def redis_container():
    container = RedisContainer("redis:7-alpine")
    container.start()
    yield container
    container.stop()


@pytest.fixture
def redis_url(redis_container) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


def _find_middleware_instances() -> list[RateLimitMiddleware]:
    """Walk Starlette's middleware chain to recover the live
    ``RateLimitMiddleware`` instance(s) attached to the ingestor app.
    """
    instances: list[RateLimitMiddleware] = []
    seen = {id(ingestor_service)}
    stack = [ingestor_service.middleware_stack]
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, RateLimitMiddleware):
            instances.append(node)
        # Starlette middleware wraps an inner ``.app`` attribute
        inner = getattr(node, "app", None)
        if inner is not None:
            stack.append(inner)
    return instances


def _reset_rate_limit_state() -> None:
    """Force the middleware to re-read the env var + reopen Redis on the
    next request. Safe to call even if no middleware instance exists yet
    (lazy init path).
    """
    # ``middleware_stack`` is built lazily on first request via
    # ``app.build_middleware_stack()``; force-build so we can poke it.
    if ingestor_service.middleware_stack is None:
        ingestor_service.middleware_stack = ingestor_service.build_middleware_stack()
    for inst in _find_middleware_instances():
        inst._redis = None
        inst._script_sha = None
        inst._redis_url_cache = None
        inst._provider_called = False
    # The per-token helper holds its own module-level Redis singleton —
    # reset that too so a future test reading os.environ doesn't keep a
    # client open against the about-to-be-stopped container.
    token_rate_limit._reset_for_tests()


@pytest.mark.asyncio
async def test_ingestor_blocks_after_limit_with_real_redis(
    monkeypatch,
    database_url,
    engine,
    tmp_path,
    redis_url,
):
    """Drive ingest above the per-IP limit; assert the next call gets 429.

    Uses the limit of 100/60s configured at module-eval in
    ``ingestor/main.py``. Sending 101 requests in a tight loop crosses
    the boundary deterministically.
    """
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", redis_url)

    from webhook_inspector.web.app import deps as app_deps
    from webhook_inspector.web.app.main import app as app_service
    from webhook_inspector.web.ingestor import deps as ing_deps

    for m in (app_deps, ing_deps):
        m.get_settings.cache_clear()
        m._engine.cache_clear()
        m._session_factory.cache_clear()

    _reset_rate_limit_state()
    try:
        # Create an endpoint via the web service.
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app_service), base_url="http://test"
        ) as c:
            resp = await c.post("/api/endpoints")
            token = resp.json()["token"]

        # Hit the ingestor 100 times (allowed) + 1 more (blocked).
        # All from the same IP. ASGITransport plumbs request.client.host
        # as "testclient" → the middleware keys by that string.
        blocked_count = 0
        async with httpx.AsyncClient(
            transport=ASGITransport(app=ingestor_service), base_url="http://hook"
        ) as c:
            for _ in range(101):
                r = await c.post(f"/h/{token}", content=b"x")
                if r.status_code == 429:
                    blocked_count += 1
                    assert "Retry-After" in r.headers

        assert blocked_count >= 1, "expected at least one 429 after crossing 100/60s"
    finally:
        # Critical: reset the middleware's cached Redis client and URL so
        # the next test (which doesn't set RATE_LIMIT_REDIS_URL) won't
        # keep hitting the about-to-be-torn-down container.
        _reset_rate_limit_state()
