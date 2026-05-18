from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.fakes import (
    FakeBlobStorage,
    FakeEndpointRepo,
    FakeForwardQueue,
    FakeForwardRepository,
    FakeMetricsCollector,
    FakeRequestRepo,
)
from webhook_inspector.application.use_cases.capture_request import (
    CaptureRequest,
    EndpointNotFoundError,
)
from webhook_inspector.domain.entities.endpoint import Endpoint

# Dummy 32-byte key; these tests don't configure a signature provider so the
# key is never actually used for encryption/decryption.
_NO_KEY = b"\x00" * 32


def _make_endpoint(*, forward_url: str | None = None) -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token="abc",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
        forward_url=forward_url,
    )


def _make_uc(
    erepo: FakeEndpointRepo,
    rrepo: FakeRequestRepo | None = None,
    blob: FakeBlobStorage | None = None,
    metrics: FakeMetricsCollector | None = None,
    forward_repo: FakeForwardRepository | None = None,
    forward_queue: FakeForwardQueue | None = None,
) -> CaptureRequest:
    return CaptureRequest(
        endpoint_repo=erepo,
        request_repo=rrepo or FakeRequestRepo(),
        blob_storage=blob or FakeBlobStorage(),
        inline_threshold=8192,
        metrics=metrics or FakeMetricsCollector(),
        secrets_key=_NO_KEY,
        forward_repo=forward_repo or FakeForwardRepository(),
        forward_queue=forward_queue or FakeForwardQueue(),
    )


async def test_capture_small_body_inline():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = _make_uc(erepo, rrepo, blob)

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
    uc = _make_uc(erepo, rrepo, blob)

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
    uc = _make_uc(erepo, rrepo, blob)

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
    uc = _make_uc(erepo)

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
    uc = _make_uc(erepo)

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
    metrics = FakeMetricsCollector()
    uc = _make_uc(erepo, metrics=metrics)

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
    uc = _make_uc(erepo)

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
    uc = _make_uc(erepo)

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
    uc = _make_uc(erepo)

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
    blob = FakeBlobStorage(fail=True)  # R2 down
    uc = _make_uc(erepo, blob=blob)

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


# ---------------------------------------------------------------------------
# Forward enqueue
# ---------------------------------------------------------------------------


async def test_forward_enqueued_when_endpoint_has_forward_url():
    ep = _make_endpoint(forward_url="https://example.com/wh")
    erepo = FakeEndpointRepo(ep)
    fwd_repo = FakeForwardRepository()
    fwd_queue = FakeForwardQueue()
    uc = _make_uc(erepo, forward_repo=fwd_repo, forward_queue=fwd_queue)

    await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"hello",
        source_ip="1.2.3.4",
    )

    assert len(fwd_repo.saved) == 1
    assert fwd_repo.saved[0].target_url == "https://example.com/wh"
    assert fwd_repo.saved[0].status == "pending"
    assert len(fwd_queue.enqueued) == 1
    assert fwd_queue.enqueued[0][0] == fwd_repo.saved[0].id


async def test_forward_not_enqueued_when_endpoint_has_no_forward_url():
    ep = _make_endpoint()  # no forward_url
    erepo = FakeEndpointRepo(ep)
    fwd_repo = FakeForwardRepository()
    fwd_queue = FakeForwardQueue()
    uc = _make_uc(erepo, forward_repo=fwd_repo, forward_queue=fwd_queue)

    await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"hello",
        source_ip="1.2.3.4",
    )

    assert len(fwd_repo.saved) == 0
    assert len(fwd_queue.enqueued) == 0


async def test_forward_enqueue_failure_does_not_crash_capture():
    """If enqueue raises (e.g. Redis down), capture still succeeds."""

    class BrokenForwardQueue(FakeForwardQueue):
        async def enqueue(self, forward_id, *, defer_seconds: int = 0) -> None:
            raise RuntimeError("redis connection refused")

    ep = _make_endpoint(forward_url="https://example.com/wh")
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    fwd_repo = FakeForwardRepository()
    fwd_queue = BrokenForwardQueue()
    uc = _make_uc(erepo, rrepo, forward_repo=fwd_repo, forward_queue=fwd_queue)

    # Must not raise
    _captured, _ = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"hello",
        source_ip="1.2.3.4",
    )

    # Capture persisted
    assert len(rrepo.saved) == 1
    # Forward row was saved before the failed enqueue
    assert len(fwd_repo.saved) == 1


async def test_forward_enqueue_failure_increments_failure_metric():
    """If enqueue raises, forward_enqueue_failed metric is incremented."""

    class BrokenForwardQueue(FakeForwardQueue):
        async def enqueue(self, forward_id, *, defer_seconds: int = 0) -> None:
            raise RuntimeError("redis connection refused")

    ep = _make_endpoint(forward_url="https://example.com/wh")
    erepo = FakeEndpointRepo(ep)
    metrics = FakeMetricsCollector()
    fwd_repo = FakeForwardRepository()
    fwd_queue = BrokenForwardQueue()
    uc = _make_uc(erepo, metrics=metrics, forward_repo=fwd_repo, forward_queue=fwd_queue)

    await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"hello",
        source_ip="1.2.3.4",
    )

    assert metrics.forward_enqueue_failed_count == 1
