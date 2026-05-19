"""Unit tests for enforce_token_limit (the per-token Redis Lua helper).

The helper holds a module-level Redis client + cached SHA, so each test
resets that state via ``token_rate_limit._reset_for_tests`` before
patching ``redis.from_url`` with an AsyncMock.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from tests.fakes.metrics_collector import FakeMetricsCollector
from webhook_inspector.web.middleware import token_rate_limit
from webhook_inspector.web.middleware.token_rate_limit import enforce_token_limit


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Drop the module-level Redis singleton before AND after each test."""
    token_rate_limit._reset_for_tests()
    yield
    token_rate_limit._reset_for_tests()


def _redis_mock(evalsha_return: list[int] | Exception) -> AsyncMock:
    m = AsyncMock()
    m.script_load = AsyncMock(return_value="stubsha")
    if isinstance(evalsha_return, Exception):
        m.evalsha = AsyncMock(side_effect=evalsha_return)
    else:
        m.evalsha = AsyncMock(return_value=evalsha_return)
    return m


@pytest.mark.asyncio
async def test_under_limit_no_raise(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "redis://stub")
    redis_mock = _redis_mock([1, 3, 0])
    monkeypatch.setattr(
        "webhook_inspector.web.middleware.token_rate_limit.redis.from_url",
        lambda *_a, **_kw: redis_mock,
    )
    metrics = FakeMetricsCollector()
    await enforce_token_limit(
        token="tok-abc",
        rule_name="replay",
        limit=10,
        window_seconds=3600,
        metrics=metrics,
    )
    assert metrics.rate_limit_block_calls == []
    redis_mock.evalsha.assert_awaited_once()
    # key shape: rl:replay:tok-abc (bytes)
    call_args = redis_mock.evalsha.await_args
    assert call_args.args[2] == b"rl:replay:tok-abc"


@pytest.mark.asyncio
async def test_over_limit_raises_429(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "redis://stub")
    redis_mock = _redis_mock([0, 10, 42])
    monkeypatch.setattr(
        "webhook_inspector.web.middleware.token_rate_limit.redis.from_url",
        lambda *_a, **_kw: redis_mock,
    )
    metrics = FakeMetricsCollector()
    with pytest.raises(HTTPException) as excinfo:
        await enforce_token_limit(
            token="tok-abc",
            rule_name="replay",
            limit=10,
            window_seconds=3600,
            metrics=metrics,
        )
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == "rate limit exceeded"
    assert excinfo.value.headers == {"Retry-After": "42"}
    assert len(metrics.rate_limit_block_calls) == 1
    assert metrics.rate_limit_block_calls[0].rule == "replay"
    assert metrics.rate_limit_block_calls[0].reason == "quota"


@pytest.mark.asyncio
async def test_redis_error_fail_open(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "redis://stub")
    redis_mock = _redis_mock(RuntimeError("redis down"))
    monkeypatch.setattr(
        "webhook_inspector.web.middleware.token_rate_limit.redis.from_url",
        lambda *_a, **_kw: redis_mock,
    )
    metrics = FakeMetricsCollector()
    # No exception expected — fail-open semantics for the per-token surface.
    await enforce_token_limit(
        token="tok-abc",
        rule_name="capture",
        limit=1000,
        window_seconds=3600,
        metrics=metrics,
    )
    assert metrics.rate_limit_redis_error_calls == ["capture"]
    assert metrics.rate_limit_block_calls == []


@pytest.mark.asyncio
async def test_dev_mode_no_url_no_raise(monkeypatch):
    """When RATE_LIMIT_REDIS_URL is unset, the helper must short-circuit
    without instantiating Redis at all.
    """
    monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)
    from_url_mock = AsyncMock()
    monkeypatch.setattr(
        "webhook_inspector.web.middleware.token_rate_limit.redis.from_url",
        from_url_mock,
    )
    metrics = FakeMetricsCollector()
    await enforce_token_limit(
        token="tok-abc",
        rule_name="replay",
        limit=10,
        window_seconds=3600,
        metrics=metrics,
    )
    from_url_mock.assert_not_called()
    assert metrics.rate_limit_block_calls == []
    assert metrics.rate_limit_redis_error_calls == []


@pytest.mark.asyncio
async def test_key_shape_uses_rule_name_and_token(monkeypatch):
    """Different rule_names + tokens hit DIFFERENT keys, never collapse."""
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "redis://stub")
    redis_mock = _redis_mock([1, 1, 0])
    monkeypatch.setattr(
        "webhook_inspector.web.middleware.token_rate_limit.redis.from_url",
        lambda *_a, **_kw: redis_mock,
    )
    metrics = FakeMetricsCollector()
    await enforce_token_limit(
        token="tok-A", rule_name="replay", limit=10, window_seconds=60, metrics=metrics
    )
    await enforce_token_limit(
        token="tok-B", rule_name="capture", limit=10, window_seconds=60, metrics=metrics
    )
    keys = [call.args[2] for call in redis_mock.evalsha.await_args_list]
    assert keys == [b"rl:replay:tok-A", b"rl:capture:tok-B"]
