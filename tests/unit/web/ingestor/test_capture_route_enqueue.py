"""Unit tests for BackgroundTasks-based enqueue in the ingestor capture route.

These tests confirm that schema_queue.enqueue is called post-commit (via
BackgroundTasks) under the correct conditions, and that enqueue failures do
not propagate to the caller.

No database required — CaptureRequest is fully replaced by a stub.
FastAPI's TestClient executes background tasks before returning, so
FakeSchemaQueue.enqueued is populated by the time await client.post() returns.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from tests.fakes import FakeMetricsCollector, FakeSchemaQueue
from webhook_inspector.config import Settings
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.web.ingestor.deps import get_metrics, get_schema_queue, get_settings


def _stub_settings() -> Settings:
    """Build a Settings instance for tests without requiring env vars.

    The capture route only reads `max_body_bytes`. Pass a placeholder for
    every required field so Pydantic validation passes.
    """
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
    )


def _make_endpoint(*, response_delay_ms: int = 0) -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token="tok",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
        response_delay_ms=response_delay_ms,
    )


def _make_captured(
    endpoint_id,
    *,
    detected_integration: str | None = None,
    detected_event_type: str | None = None,
) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=endpoint_id,
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={"stripe-signature": "t=1,v1=abc"},
        body_preview='{"type":"payment_intent.created"}',
        body_size=33,
        blob_key=None,
        source_ip="127.0.0.1",
        received_at=datetime.now(UTC),
        detected_integration=detected_integration,
        detected_event_type=detected_event_type,
    )


class _StubCaptureUseCase:
    """Minimal stub replacing CaptureRequest use case."""

    def __init__(self, captured: CapturedRequest, endpoint: Endpoint) -> None:
        self._captured = captured
        self._endpoint = endpoint

    async def execute(self, **kwargs) -> tuple[CapturedRequest, Endpoint]:
        return self._captured, self._endpoint


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_called_when_detected_integration_is_set():
    """POST with stripe-signature → FakeSchemaQueue.enqueued has 1 entry."""
    from webhook_inspector.web.ingestor.deps import get_capture_request
    from webhook_inspector.web.ingestor.main import app

    endpoint = _make_endpoint()
    captured = _make_captured(
        endpoint.id,
        detected_integration="stripe",
        detected_event_type="payment_intent.created",
    )
    stub_uc = _StubCaptureUseCase(captured, endpoint)
    fake_queue = FakeSchemaQueue()
    fake_metrics = FakeMetricsCollector()

    app.dependency_overrides[get_capture_request] = lambda: stub_uc
    app.dependency_overrides[get_schema_queue] = lambda: fake_queue
    app.dependency_overrides[get_metrics] = lambda: fake_metrics
    app.dependency_overrides[get_settings] = _stub_settings

    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/h/tok",
                headers={"stripe-signature": "t=1,v1=abc"},
                content=b'{"type":"payment_intent.created"}',
            )
        assert resp.status_code == 200
        assert len(fake_queue.enqueued) == 1
        entry = fake_queue.enqueued[0]
        assert entry["request_id"] == captured.id
        assert entry["endpoint_id"] == endpoint.id
        assert entry["integration"] == "stripe"
        assert entry["event_type"] == "payment_intent.created"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_enqueue_not_called_when_no_integration_detected():
    """POST with no integration-triggering headers → queue stays empty."""
    from webhook_inspector.web.ingestor.deps import get_capture_request
    from webhook_inspector.web.ingestor.main import app

    endpoint = _make_endpoint()
    captured = _make_captured(endpoint.id, detected_integration=None)
    stub_uc = _StubCaptureUseCase(captured, endpoint)
    fake_queue = FakeSchemaQueue()
    fake_metrics = FakeMetricsCollector()

    app.dependency_overrides[get_capture_request] = lambda: stub_uc
    app.dependency_overrides[get_schema_queue] = lambda: fake_queue
    app.dependency_overrides[get_metrics] = lambda: fake_metrics
    app.dependency_overrides[get_settings] = _stub_settings

    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/h/tok", content=b'{"foo":"bar"}')
        assert resp.status_code == 200
        assert fake_queue.enqueued == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_enqueue_failure_does_not_propagate():
    """Queue failure → response still 200, schema_enqueue_failed counter incremented."""
    from webhook_inspector.web.ingestor.deps import get_capture_request
    from webhook_inspector.web.ingestor.main import app

    endpoint = _make_endpoint()
    captured = _make_captured(
        endpoint.id,
        detected_integration="stripe",
        detected_event_type="charge.updated",
    )
    stub_uc = _StubCaptureUseCase(captured, endpoint)
    fake_queue = FakeSchemaQueue(fail=True)
    fake_metrics = FakeMetricsCollector()

    app.dependency_overrides[get_capture_request] = lambda: stub_uc
    app.dependency_overrides[get_schema_queue] = lambda: fake_queue
    app.dependency_overrides[get_metrics] = lambda: fake_metrics
    app.dependency_overrides[get_settings] = _stub_settings

    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/h/tok",
                headers={"stripe-signature": "t=1,v1=abc"},
                content=b'{"type":"charge.updated"}',
            )
        assert resp.status_code == 200
        assert fake_metrics.schema_enqueue_failed_count == 1
    finally:
        app.dependency_overrides.clear()
