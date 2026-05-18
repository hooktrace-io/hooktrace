from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.fakes import FakeBlobStorage, FakeEndpointRepo, FakeMetricsCollector, FakeRequestRepo
from webhook_inspector.application.use_cases.capture_request import (
    CaptureRequest,
    EndpointNotFoundError,
)
from webhook_inspector.domain.entities.endpoint import Endpoint

# Dummy 32-byte key; these tests don't configure a signature provider so the
# key is never actually used for encryption/decryption.
_NO_KEY = b"\x00" * 32


def _make_endpoint() -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token="abc",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
    )


async def test_capture_small_body_inline():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    _captured, _endpoint = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"x": "y"},
        body=b"hi",
        source_ip="192.0.2.1",
    )

    assert len(rrepo.saved) == 1
    assert rrepo.saved[0].body_preview == "hi"
    assert rrepo.saved[0].blob_key is None
    assert blob.puts == {}
    assert erepo.increments == [ep.id]


async def test_capture_large_body_uploads_blob():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    big = b"x" * 10000
    captured, _endpoint = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=big,
        source_ip="192.0.2.1",
    )

    assert captured.blob_key is not None
    assert blob.puts[captured.blob_key] == big


async def test_capture_falls_back_when_blob_storage_fails():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage(fail=True)
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    big = b"x" * 10000
    captured, _endpoint = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=big,
        source_ip="192.0.2.1",
    )

    # Metadata persisted even though blob failed
    assert len(rrepo.saved) == 1
    assert captured.blob_key is None  # downgraded
    assert captured.body_size == 10000


async def test_capture_unknown_token_raises():
    erepo = FakeEndpointRepo()
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    with pytest.raises(EndpointNotFoundError):
        await uc.execute(
            token="missing",
            method="GET",
            path="/h/missing",
            query_string=None,
            headers={},
            body=b"",
            source_ip="192.0.2.1",
        )


async def test_capture_uppercases_method():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    captured, _endpoint = await uc.execute(
        token="abc",
        method="post",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"",
        source_ip="192.0.2.1",
    )

    assert captured.method == "POST"


async def test_capture_request_records_metric():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    metrics = FakeMetricsCollector()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=metrics,
        secrets_key=_NO_KEY,
    )

    await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"hi",
        source_ip="192.0.2.1",
    )

    assert len(metrics.captured_calls) == 1
    call = metrics.captured_calls[0]
    assert call.method == "POST"
    assert call.body_offloaded is False
    assert call.body_size == 2
    assert call.duration_seconds >= 0


# ---------------------------------------------------------------------------
# Integration detection
# ---------------------------------------------------------------------------


async def test_capture_detects_stripe_integration():
    """Stripe-signature header → detected_integration='stripe'."""
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    captured, _ = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"stripe-signature": "t=1,v1=abc"},
        body=b'{"type": "payment_intent.created", "id": "evt_123"}',
        source_ip="1.2.3.4",
    )

    assert captured.detected_integration == "stripe"
    assert captured.detected_event_type == "payment_intent.created"


async def test_capture_stripe_body_without_type_field():
    """Stripe body missing 'type' → event_type is None."""
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    captured, _ = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"stripe-signature": "t=1,v1=abc"},
        body=b'{"id": "evt_123"}',
        source_ip="1.2.3.4",
    )

    assert captured.detected_integration == "stripe"
    assert captured.detected_event_type is None


async def test_capture_no_integration_headers_returns_none():
    """Plain POST with no known headers → both detection fields are None."""
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    captured, _ = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"content-type": "application/json"},
        body=b'{"foo": "bar"}',
        source_ip="1.2.3.4",
    )

    assert captured.detected_integration is None
    assert captured.detected_event_type is None


async def test_capture_blob_fallback_preserves_integration():
    """When R2 upload fails, the reconstructed CapturedRequest keeps integration fields."""
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage(fail=True)  # R2 down
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_NO_KEY,
    )

    big = b'{"type": "charge.failed"}' * 500  # > 8192 bytes → triggers blob path

    captured, _ = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"stripe-signature": "t=1,v1=abc"},
        body=big,
        source_ip="1.2.3.4",
    )

    # Blob failed, so blob_key is None. But integration must still be set.
    assert captured.blob_key is None
    assert captured.detected_integration == "stripe"
    # Body > 8 KB → extract_stripe_event_type cap → None
    assert captured.detected_event_type is None
