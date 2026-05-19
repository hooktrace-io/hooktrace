"""Unit tests for the sliding-window rate-limit middleware.

Mocks redis.asyncio.Redis with AsyncMock; asserts on the Lua-script
invocation pattern, HTTP status codes, Retry-After header, and fake
metric calls. No real Redis required — the script-load + evalsha calls
are stubbed.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from tests.fakes.metrics_collector import FakeMetricsCollector
from webhook_inspector.web.middleware.rate_limit import RateLimitMiddleware, _Rule


def _build_app(
    *,
    rules: dict[str, _Rule],
    redis_url: str | None = "redis://stub",
    redis_mock: AsyncMock | None = None,
    metrics: FakeMetricsCollector | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[Starlette, FakeMetricsCollector]:
    """Build a minimal Starlette app with the middleware mounted.

    When ``redis_url`` is None, the middleware should bypass Redis
    entirely (dev mode). Otherwise ``redis_mock`` replaces
    ``redis.from_url`` so no real connection is opened.
    """
    metrics = metrics or FakeMetricsCollector()

    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/{path:path}", ok, methods=["GET", "POST"])])

    if redis_mock is not None and monkeypatch is not None:
        monkeypatch.setattr(
            "webhook_inspector.web.middleware.rate_limit.redis.from_url",
            lambda *_a, **_kw: redis_mock,
        )

    app.add_middleware(
        RateLimitMiddleware,
        redis_url_provider=lambda: redis_url,
        rules=rules,
        metrics_provider=lambda: metrics,
    )
    return app, metrics


def _redis_mock(evalsha_return: list[int] | Exception) -> AsyncMock:
    """Construct an AsyncMock that mimics a redis.asyncio.Redis client.

    ``script_load`` resolves to a stub SHA. ``evalsha`` either returns
    the supplied list (allowed/count/retry_after) or raises the supplied
    exception.
    """
    m = AsyncMock()
    m.script_load = AsyncMock(return_value="stubsha")
    if isinstance(evalsha_return, Exception):
        m.evalsha = AsyncMock(side_effect=evalsha_return)
    else:
        m.evalsha = AsyncMock(return_value=evalsha_return)
    return m


@pytest.mark.asyncio
async def test_unmatched_path_bypasses(monkeypatch):
    """A path not matching any rule prefix must NOT hit Redis."""
    redis_mock = _redis_mock([1, 0, 0])
    app, metrics = _build_app(
        rules={"/api/": _Rule(name="api", limit=10, window_seconds=60, fail_mode="open")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/healthz")
    assert resp.status_code == 200
    redis_mock.evalsha.assert_not_called()
    assert metrics.rate_limit_block_calls == []


@pytest.mark.asyncio
async def test_under_limit_allows(monkeypatch):
    """allowed=1 → the next handler runs and returns 200."""
    redis_mock = _redis_mock([1, 3, 0])
    app, metrics = _build_app(
        rules={"/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/h/abc")
    assert resp.status_code == 200
    assert resp.text == "ok"
    redis_mock.evalsha.assert_awaited_once()
    assert metrics.rate_limit_block_calls == []


@pytest.mark.asyncio
async def test_over_limit_returns_429_with_retry_after(monkeypatch):
    """allowed=0 → 429 + Retry-After: <retry_after_seconds>."""
    redis_mock = _redis_mock([0, 100, 42])
    app, _metrics = _build_app(
        rules={"/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/h/abc")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "42"
    assert resp.json() == {"detail": "rate limit exceeded"}


@pytest.mark.asyncio
async def test_redis_error_fail_closed_returns_503(monkeypatch):
    """Redis raises + rule is fail_closed → 503 + Retry-After: 60."""
    redis_mock = _redis_mock(RuntimeError("redis down"))
    app, _metrics = _build_app(
        rules={"/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/h/abc")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "60"
    assert resp.json() == {"detail": "rate limiter unavailable"}


@pytest.mark.asyncio
async def test_redis_error_fail_open_allows(monkeypatch):
    """Redis raises + rule is fail_open → request proceeds (200)."""
    redis_mock = _redis_mock(RuntimeError("redis down"))
    app, _metrics = _build_app(
        rules={"/api/": _Rule(name="api", limit=10, window_seconds=60, fail_mode="open")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/endpoints")
    assert resp.status_code == 200
    assert resp.text == "ok"


@pytest.mark.asyncio
async def test_dev_mode_no_redis_url_bypasses(monkeypatch):
    """When the provider returns None, request must pass through with NO
    Redis instantiation at all. Verifies the dev-mode bypass.
    """
    from_url_mock = MagicMock()
    monkeypatch.setattr(
        "webhook_inspector.web.middleware.rate_limit.redis.from_url",
        from_url_mock,
    )
    app, _metrics = _build_app(
        rules={"/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed")},
        redis_url=None,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/h/abc")
    assert resp.status_code == 200
    from_url_mock.assert_not_called()


@pytest.mark.asyncio
async def test_metric_emitted_on_quota_block(monkeypatch):
    redis_mock = _redis_mock([0, 100, 5])
    app, metrics = _build_app(
        rules={"/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/h/abc")
    assert len(metrics.rate_limit_block_calls) == 1
    assert metrics.rate_limit_block_calls[0].rule == "ingest"
    assert metrics.rate_limit_block_calls[0].reason == "quota"


@pytest.mark.asyncio
async def test_metric_emitted_on_redis_error(monkeypatch):
    redis_mock = _redis_mock(RuntimeError("boom"))
    app, metrics = _build_app(
        rules={"/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/h/abc")
    # Both metrics fire: one redis_error, one block(reason=fail_closed)
    assert metrics.rate_limit_redis_error_calls == ["ingest"]
    assert len(metrics.rate_limit_block_calls) == 1
    assert metrics.rate_limit_block_calls[0].reason == "fail_closed"


@pytest.mark.asyncio
async def test_lua_invocation_uses_unique_member_per_call(monkeypatch):
    """Two requests in the same millisecond must produce DIFFERENT
    ZADD members (the UUID suffix), otherwise ZADD's overwrite semantics
    collapse the burst.
    """
    redis_mock = _redis_mock([1, 1, 0])
    app, _metrics = _build_app(
        rules={"/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/h/abc")
        await c.post("/h/abc")
    assert redis_mock.evalsha.await_count == 2
    # Member is positional arg index 6 (after sha, numkeys=1, key, now, window, limit).
    members = [call.args[6] for call in redis_mock.evalsha.await_args_list]
    assert len(members) == 2
    assert members[0] != members[1]
    # Each member is "<ms>:<hex>" — 32 hex chars after the colon.
    for m in members:
        ms_part, _, hex_part = m.partition(":")
        assert ms_part.isdigit()
        assert len(hex_part) == 32


@pytest.mark.asyncio
async def test_first_request_loads_script_once(monkeypatch):
    """Lua script_load is called on the very first request and never
    again — script SHA is cached on the middleware instance.
    """
    redis_mock = _redis_mock([1, 1, 0])
    app, _metrics = _build_app(
        rules={"/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed")},
        redis_mock=redis_mock,
        monkeypatch=monkeypatch,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/h/a")
        await c.post("/h/b")
        await c.post("/h/c")
    redis_mock.script_load.assert_awaited_once()
