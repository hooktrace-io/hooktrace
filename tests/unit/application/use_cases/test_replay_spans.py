"""Confirmatory span tests for ReplayRequest.execute().

These tests verify that the use case emits the expected OTEL spans without
altering any business logic.

Strategy: install one TracerProvider for the entire module (the global proxy
only accepts the first real provider). Each test adds a fresh
InMemorySpanExporter so span assertions are isolated.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.fakes import (
    FakeBlobStorage,
    FakeEndpointRepo,
    FakeHttpReplayTarget,
    FakeMetricsCollector,
    FakeReplayRepository,
    FakeRequestRepo,
)
from webhook_inspector.application.use_cases.replay_request import ReplayRequest
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.ports.http_replay_target import SsrfBlockedError

# span_exporter fixture is provided by conftest.py in this package.


def _endpoint(token: str = "tok-abc") -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token=token,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
    )


def _captured(endpoint_id, *, body_preview: str = "hello", blob_key: str | None = None):
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=endpoint_id,
        method="POST",
        path="/hook",
        query_string=None,
        headers={"Content-Type": "application/json"},
        body_preview=body_preview,
        body_size=len(body_preview.encode()) if body_preview else 0,
        blob_key=blob_key,
        source_ip="1.2.3.4",
        received_at=datetime.now(UTC),
    )


async def test_replay_emits_replay_span(span_exporter: InMemorySpanExporter):
    """Successful replay produces 'replay', 'endpoint.lookup', 'request.lookup',
    'ssrf.validate', 'http.send', and 'db.insert' spans.
    """
    ep = _endpoint()
    req = _captured(ep.id)
    uc = ReplayRequest(
        endpoint_repo=FakeEndpointRepo(seed=ep),
        request_repo=FakeRequestRepo(items=[req]),
        replay_repo=FakeReplayRepository(),
        target=FakeHttpReplayTarget(),
        blob_storage=FakeBlobStorage(),
        metrics=FakeMetricsCollector(),
    )

    replay = await uc.execute(
        token=ep.token,
        request_id=req.id,
        target_url="https://example.com/webhook",
    )

    assert replay.status_code == 200
    names = [s.name for s in span_exporter.get_finished_spans()]
    assert "replay" in names
    assert "endpoint.lookup" in names
    assert "request.lookup" in names
    assert "ssrf.validate" in names
    assert "http.send" in names
    assert "db.insert" in names


async def test_replay_ssrf_block_records_reason_attribute(span_exporter: InMemorySpanExporter):
    """When SSRF guard raises, ssrf.validate span has blocked=True and reason set."""
    ep = _endpoint()
    req = _captured(ep.id)
    target = FakeHttpReplayTarget()
    target.raise_on_validate(SsrfBlockedError("private IP range"))

    uc = ReplayRequest(
        endpoint_repo=FakeEndpointRepo(seed=ep),
        request_repo=FakeRequestRepo(items=[req]),
        replay_repo=FakeReplayRepository(),
        target=target,
        blob_storage=FakeBlobStorage(),
        metrics=FakeMetricsCollector(),
    )

    replay = await uc.execute(
        token=ep.token,
        request_id=req.id,
        target_url="http://192.168.1.1/webhook",
    )

    assert replay.error is not None
    finished: list[ReadableSpan] = span_exporter.get_finished_spans()
    ssrf_spans = [s for s in finished if s.name == "ssrf.validate"]
    assert len(ssrf_spans) == 1
    attrs = ssrf_spans[0].attributes or {}
    assert attrs.get("blocked") is True
    assert attrs.get("reason") == "private IP range"
