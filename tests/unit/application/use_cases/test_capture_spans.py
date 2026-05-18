"""Confirmatory span tests for CaptureRequest.execute().

These tests verify that the use case emits the expected OTEL spans without
altering any business logic.

Strategy: install one TracerProvider for the entire module (the global proxy
only accepts the first real provider, so we can't replace it per test). Each
test adds a fresh InMemorySpanExporter via add_span_processor, runs the use
case, then reads only the spans from that exporter.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.fakes import (
    FakeBlobStorage,
    FakeEndpointRepo,
    FakeMetricsCollector,
    FakeRequestRepo,
)
from webhook_inspector.application.use_cases.capture_request import CaptureRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import EndpointNotFoundError

_TEST_KEY = b"\x00" * 32
# span_exporter fixture is provided by conftest.py in this package.


def _make_endpoint(
    *,
    signature_provider: str | None = None,
    signature_secret_encrypted: bytes | None = None,
) -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token="tok",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
        signature_provider=signature_provider,
        signature_secret_encrypted=signature_secret_encrypted,
    )


def _make_use_case(endpoint: Endpoint) -> CaptureRequest:
    return CaptureRequest(
        endpoint_repo=FakeEndpointRepo(endpoint),
        request_repo=FakeRequestRepo(),
        blob_storage=FakeBlobStorage(),
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_TEST_KEY,
    )


async def test_capture_emits_capture_span(span_exporter: InMemorySpanExporter):
    """Successful capture produces 'capture', 'endpoint.lookup',
    'integration.detect', and 'db.insert' spans.
    """
    endpoint = _make_endpoint()
    uc = _make_use_case(endpoint)

    await uc.execute(
        token="tok",
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={},
        body=b"hello",
        source_ip="127.0.0.1",
    )

    names = [s.name for s in span_exporter.get_finished_spans()]
    assert "capture" in names
    assert "endpoint.lookup" in names
    assert "integration.detect" in names
    assert "db.insert" in names


async def test_capture_endpoint_not_found_records_result_attribute(
    span_exporter: InMemorySpanExporter,
):
    """When endpoint is not found the capture span has result=endpoint_not_found."""
    uc = CaptureRequest(
        endpoint_repo=FakeEndpointRepo(),  # no seed → returns None
        request_repo=FakeRequestRepo(),
        blob_storage=FakeBlobStorage(),
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_TEST_KEY,
    )

    with pytest.raises(EndpointNotFoundError):
        await uc.execute(
            token="unknown",
            method="GET",
            path="/h/unknown",
            query_string=None,
            headers={},
            body=b"",
            source_ip="127.0.0.1",
        )

    finished = span_exporter.get_finished_spans()
    capture_spans = [s for s in finished if s.name == "capture"]
    assert len(capture_spans) == 1
    attrs = capture_spans[0].attributes or {}
    assert attrs.get("result") == "endpoint_not_found"
