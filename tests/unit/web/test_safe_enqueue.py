"""Pin the post-commit enqueue helper's failure contract.

`_safe_enqueue` runs inside a FastAPI BackgroundTask, AFTER the response
has been sent and the DB transaction committed. It must:

1. Never raise (would surface as an unhandled error in the worker
   that runs background tasks — visible in logs but not user-facing).
2. Increment `forward_enqueue_failed` on failure so operators see the
   failure rate; otherwise Redis flaps would be invisible to alerts.
3. Use `logger.exception` so the traceback lands in structured logs.

Tests cover both the ingestor (capture) and web app (retry) variants.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from tests.fakes import FakeForwardQueue
from tests.fakes.metrics_collector import FakeMetricsCollector
from webhook_inspector.web.app.routes import _safe_enqueue as app_safe_enqueue
from webhook_inspector.web.ingestor.routes import _safe_enqueue as ingest_safe_enqueue


@pytest.mark.asyncio
async def test_ingestor_safe_enqueue_success_does_not_touch_metric() -> None:
    queue = FakeForwardQueue()
    metrics = FakeMetricsCollector()
    fid = uuid.uuid4()

    await ingest_safe_enqueue(queue, metrics, fid)

    assert queue.enqueued == [(fid, 0)]
    assert metrics.forward_enqueue_failed_count == 0


@pytest.mark.asyncio
async def test_ingestor_safe_enqueue_failure_increments_metric() -> None:
    failing_queue = AsyncMock()
    failing_queue.enqueue.side_effect = RuntimeError("redis is down")
    metrics = FakeMetricsCollector()
    fid = uuid.uuid4()

    # Must not raise — background task swallows the error.
    await ingest_safe_enqueue(failing_queue, metrics, fid)

    assert metrics.forward_enqueue_failed_count == 1


@pytest.mark.asyncio
async def test_app_safe_enqueue_success_does_not_touch_metric() -> None:
    queue = FakeForwardQueue()
    metrics = FakeMetricsCollector()
    fid = uuid.uuid4()

    await app_safe_enqueue(queue, metrics, fid)

    assert queue.enqueued == [(fid, 0)]
    assert metrics.forward_enqueue_failed_count == 0


@pytest.mark.asyncio
async def test_app_safe_enqueue_failure_increments_metric() -> None:
    failing_queue = AsyncMock()
    failing_queue.enqueue.side_effect = RuntimeError("redis is down")
    metrics = FakeMetricsCollector()
    fid = uuid.uuid4()

    await app_safe_enqueue(failing_queue, metrics, fid)

    assert metrics.forward_enqueue_failed_count == 1
