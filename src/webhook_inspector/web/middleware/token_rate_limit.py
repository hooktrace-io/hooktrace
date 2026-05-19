"""Per-token rate limit, called from route handlers (NOT from middleware).

Middleware sees URL path as a string; extracting ``{token}`` requires
FastAPI's already-resolved path params, which are only available inside
the route. So we expose a tiny helper that runs the SAME Lua script as
the middleware, but with a different key shape:
``rl:{rule_name}:{token}`` instead of ``rl:{rule_name}:{client_ip}``.

On cap, raises HTTPException(429) with Retry-After. On Redis error this
is on the owner-facing surface (replay) or on a capture path whose
abuse-vector exposure is already covered by the IP-keyed middleware —
either way we fail-open (allow) and emit a redis_error metric so the
on-call can see it.
"""

import os
import time
from collections.abc import Awaitable
from typing import Any, cast
from uuid import uuid4

import redis.asyncio as redis
from fastapi import HTTPException

from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.web.middleware.rate_limit import _LUA_SLIDING_WINDOW

# Module-level cache. Both this helper and the middleware can share the
# same RATE_LIMIT_REDIS_URL — they don't need to share the actual Redis
# client because Redis itself coordinates the keys. Lazy init on first
# call so tests that never set the env var pay no connection cost.
_redis: redis.Redis | None = None
_script_sha: str | None = None


async def _ensure_redis() -> redis.Redis | None:
    global _redis, _script_sha
    url = os.environ.get("RATE_LIMIT_REDIS_URL")
    if url is None:
        return None
    if _redis is None:
        from_url = cast(Any, redis.from_url)
        _redis = cast(redis.Redis, from_url(url, decode_responses=False))
        _script_sha = cast(str, await cast(Awaitable[Any], _redis.script_load(_LUA_SLIDING_WINDOW)))
    return _redis


def _reset_for_tests() -> None:
    """Drop the module-level Redis client + cached SHA. Called from test
    fixtures so an AsyncMock injected by monkeypatch is picked up fresh.
    Not part of the public API.
    """
    global _redis, _script_sha
    _redis = None
    _script_sha = None


async def enforce_token_limit(
    *,
    token: str,
    rule_name: str,
    limit: int,
    window_seconds: int,
    metrics: MetricsCollector,
) -> None:
    try:
        r = await _ensure_redis()
        if r is None:
            return  # dev mode — no rate limit
        now_ms = int(time.time() * 1000)
        member = f"{now_ms}:{uuid4().hex}"
        key = f"rl:{rule_name}:{token}".encode()
        evalsha = cast(Any, r.evalsha)
        allowed, _count, retry_after = await evalsha(
            _script_sha,
            1,
            key,
            now_ms,
            window_seconds * 1000,
            limit,
            member,
        )
    except Exception:  # noqa: BLE001 — any Redis-side failure falls through to fail-open + metric
        metrics.rate_limit_redis_error(rule=rule_name)
        return  # fail-open
    if not allowed:
        metrics.rate_limit_block(rule=rule_name, reason="quota")
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
