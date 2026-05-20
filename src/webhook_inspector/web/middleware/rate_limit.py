"""Sliding-window rate limit via Redis. Atomic check-and-increment via a
Lua script so we never race between INCR and EXPIRE.

Algorithm: a sorted set per (key, window) holds timestamps of recent
requests. On each request, remove entries older than ``now - window``,
then add the current timestamp, then count. Block if count >= limit.

This is slightly more expensive than fixed-window INCR (~150 us / req at
the Upstash hop) but it eliminates boundary bursts, which is the whole
point of a rate limit.

Why homemade and not slowapi:
- slowapi silently falls back to in-memory if ``RATELIMIT_STORAGE_URI``
  is unset — that breaks the moment Fly auto-scales the web/ingestor.
- slowapi's BaseHTTPMiddleware counts SSE chunks as separate requests.
- slowapi defaults to fixed-window which allows 2x limit at the boundary.

We need: sliding window, atomic INCR+TTL, fail-mode per path. ~40 LOC.

Pure ASGI middleware — does NOT subclass ``BaseHTTPMiddleware``. The
``BaseHTTPMiddleware`` wrapper consumes the request body via async
iteration, which breaks FastAPIInstrumentor's HTTP server span lifecycle
(see https://github.com/open-telemetry/opentelemetry-python-contrib/issues/831).
Implementing the raw ASGI ``__call__(scope, receive, send)`` interface
leaves the request lifecycle intact so OTEL instrumentation can wrap
requests correctly.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import uuid4

import redis.asyncio as redis
from starlette.types import ASGIApp, Receive, Scope, Send

from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.web.middleware.client_ip import extract_client_ip

# Lua script: ZREMRANGEBYSCORE old + ZADD + ZCARD + EXPIRE.
# KEYS[1] = the sorted-set key (e.g. "rl:ingest:1.2.3.4")
# ARGV[1] = now in milliseconds
# ARGV[2] = window in milliseconds
# ARGV[3] = limit (int)
# ARGV[4] = unique member (e.g. "{now_ms}:{uuid4().hex}") — REQUIRED to
#          prevent ZADD overwrite on same-millisecond bursts.
# Returns: { allowed (0/1), current_count, retry_after_seconds }
_LUA_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

local cutoff = now - window
redis.call("ZREMRANGEBYSCORE", key, "-inf", cutoff)

local count = redis.call("ZCARD", key)
if count >= limit then
    local oldest = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
    local retry_ms = (tonumber(oldest[2]) + window) - now
    return {0, count, math.ceil(retry_ms / 1000)}
end

redis.call("ZADD", key, now, member)
redis.call("PEXPIRE", key, window)
return {1, count + 1, 0}
"""


@dataclass(frozen=True)
class _Rule:
    name: str
    limit: int
    window_seconds: int
    fail_mode: Literal["open", "closed"]


class RateLimitMiddleware:
    """Pure ASGI middleware — does NOT extend BaseHTTPMiddleware so FastAPI
    OTEL instrumentation can wrap requests without body-consumption conflict.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        redis_url_provider: Callable[[], str | None],
        rules: dict[str, _Rule],
        metrics_provider: Callable[[], MetricsCollector],
    ) -> None:
        self.app = app
        self._rules = rules
        self._redis_url_provider = redis_url_provider
        self._metrics_provider = metrics_provider
        self._redis: redis.Redis | None = None
        self._script_sha: str | None = None
        self._redis_url_cache: str | None = None
        self._provider_called = False

    async def _ensure_redis(self) -> redis.Redis | None:
        if not self._provider_called:
            self._redis_url_cache = self._redis_url_provider()
            self._provider_called = True
        if self._redis_url_cache is None:
            return None
        if self._redis is None:
            # redis-py marks from_url as an untyped factory; the runtime
            # return is a real Redis client. Route through Any then cast.
            from_url = cast(Any, redis.from_url)
            self._redis = cast(
                redis.Redis,
                from_url(self._redis_url_cache, decode_responses=False),
            )
            self._script_sha = cast(
                str, await cast(Awaitable[Any], self._redis.script_load(_LUA_SLIDING_WINDOW))
            )
        return self._redis

    def _match_rule(self, path: str) -> _Rule | None:
        for prefix, rule in self._rules.items():
            if path.startswith(prefix):
                return rule
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Non-HTTP (websocket, lifespan) → bypass.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = cast(str, scope.get("path", ""))
        rule = self._match_rule(path)
        if rule is None:
            await self.app(scope, receive, send)
            return

        # ASGI headers come as list[tuple[bytes, bytes]], already lowercased
        # by Starlette/uvicorn — defensive .lower() preserves correctness if
        # a non-conforming server is ever swapped in.
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        # scope["client"] is (host, port) | None.
        client = scope.get("client")
        client_host = client[0] if client else None
        client_ip = extract_client_ip(headers, client_host)

        key = f"rl:{rule.name}:{client_ip}".encode()
        metrics = self._metrics_provider()

        try:
            r = await self._ensure_redis()
            if r is None:
                # Dev mode — rate limit disabled.
                await self.app(scope, receive, send)
                return
            now_ms = int(time.time() * 1000)
            member = f"{now_ms}:{uuid4().hex}"
            # evalsha is typed strictly (str only) but redis-py accepts
            # bytes/int at runtime via its encoder. Pass through a cast.
            evalsha = cast(Any, r.evalsha)
            allowed, _count, retry_after = await evalsha(
                self._script_sha,
                1,
                key,
                now_ms,
                rule.window_seconds * 1000,
                rule.limit,
                member,
            )
        except Exception:  # noqa: BLE001 — any Redis-side failure routes through the rule's fail_mode
            metrics.rate_limit_redis_error(rule=rule.name)
            if rule.fail_mode == "closed":
                metrics.rate_limit_block(rule=rule.name, reason="fail_closed")
                await self._send_json_response(
                    send,
                    status=503,
                    body=b'{"detail":"rate limiter unavailable"}',
                    extra_headers=[(b"retry-after", b"60")],
                )
                return
            # fail_mode="open" → fall through.
            await self.app(scope, receive, send)
            return

        if not allowed:
            metrics.rate_limit_block(rule=rule.name, reason="quota")
            await self._send_json_response(
                send,
                status=429,
                body=b'{"detail":"rate limit exceeded"}',
                extra_headers=[(b"retry-after", str(retry_after).encode())],
            )
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_json_response(
        send: Send,
        *,
        status: int,
        body: bytes,
        extra_headers: list[tuple[bytes, bytes]],
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    *extra_headers,
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )
