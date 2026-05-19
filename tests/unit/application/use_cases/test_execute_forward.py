"""Unit tests for ExecuteForward use case.

Covers all 13 spec branches:
  1.  Happy path 200 — succeeded, no re-enqueue
  2.  Retryable 503 first attempt — failed + re-enqueued with 30s
  3.  Network error first attempt — failed + re-enqueued
  4.  5th attempt 503 — dead (budget exhausted), no re-enqueue
  5.  4xx hard fail (404) — dead immediately, no re-enqueue
  6.  SSRF block — dead with SsrfBlockedError message, no re-enqueue
  7.  Claim fails (duplicate fire) — skipped, no side effects
  8.  Endpoint deleted (TTL) — dead, no re-enqueue
  9.  Request deleted (TTL) — dead, no re-enqueue
  10. HMAC signature applied when endpoint has forward_secret_encrypted
  11. Body fetched from R2 when blob_key set
  12. Header fusion — Authorization stripped from capture; endpoint adds its own
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tests.fakes import (
    FakeBlobStorage,
    FakeEndpointRepo,
    FakeForwardQueue,
    FakeForwardRepository,
    FakeHttpReplayTarget,
    FakeMetricsCollector,
    FakeRequestRepo,
)
from webhook_inspector.application.use_cases.execute_forward import ExecuteForward
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.ports.http_replay_target import (
    HttpRequestFailedError,
    SsrfBlockedError,
)
from webhook_inspector.infrastructure.crypto.secrets import encrypt_secret

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_SECRETS_KEY = b"A" * 32  # 32 bytes, valid AES-256 key
_TARGET_URL = "https://example.com/forward"
_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def _endpoint(
    *,
    forward_headers: dict[str, str] | None = None,
    forward_secret_encrypted: bytes | None = None,
) -> Endpoint:
    ep_id = uuid4()
    return Endpoint(
        id=ep_id,
        token="tok-fwd",
        created_at=_NOW,
        expires_at=_NOW + timedelta(days=7),
        request_count=0,
        forward_url=_TARGET_URL,
        forward_headers=forward_headers,
        forward_secret_encrypted=forward_secret_encrypted,
    )


def _captured(
    endpoint_id: UUID,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    body_preview: str | None = '{"event":"test"}',
    blob_key: str | None = None,
) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=endpoint_id,
        method=method,
        path="/hook",
        query_string=None,
        headers=headers if headers is not None else {"Content-Type": "application/json"},
        body_preview=body_preview,
        body_size=len(body_preview.encode()) if body_preview else 0,
        blob_key=blob_key,
        source_ip="1.2.3.4",
        received_at=_NOW,
    )


def _forward(
    endpoint_id: UUID, request_id: UUID, *, attempt_count: int = 0, status: str = "pending"
) -> Forward:
    return Forward(
        id=uuid4(),
        request_id=request_id,
        endpoint_id=endpoint_id,
        target_url=_TARGET_URL,
        status=status,  # type: ignore[arg-type]
        attempt_count=attempt_count,
        last_attempt_at=None,
        next_attempt_at=_NOW,
        final_status_code=None,
        final_error=None,
        forward_started_at=None,
        forward_completed_at=None,
        created_at=_NOW,
        manual_retry_at=None,
    )


def _use_case(
    *,
    endpoint_repo: FakeEndpointRepo | None = None,
    request_repo: FakeRequestRepo | None = None,
    forward_repo: FakeForwardRepository | None = None,
    forward_queue: FakeForwardQueue | None = None,
    target: FakeHttpReplayTarget | None = None,
    blob_storage: FakeBlobStorage | None = None,
    metrics: FakeMetricsCollector | None = None,
    secrets_key: bytes = _SECRETS_KEY,
) -> ExecuteForward:
    return ExecuteForward(
        endpoint_repo=endpoint_repo or FakeEndpointRepo(),
        request_repo=request_repo or FakeRequestRepo(),
        forward_repo=forward_repo or FakeForwardRepository(),
        forward_queue=forward_queue or FakeForwardQueue(),
        target=target or FakeHttpReplayTarget(),
        blob_storage=blob_storage or FakeBlobStorage(),
        metrics=metrics or FakeMetricsCollector(),
        secrets_key=secrets_key,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_200() -> None:
    """200 response → succeeded, no re-enqueue, metric succeeded."""
    ep = _endpoint()
    req = _captured(ep.id)
    fwd = _forward(ep.id, req.id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    target.respond(status=200, body=b"ok", headers={})
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    outcome = await fwd_repo.find_by_id(fwd.id)
    assert outcome is not None
    assert outcome.status == "succeeded"
    assert outcome.final_status_code == 200
    assert queue.enqueued == []
    assert metrics.forward_attempt_calls == ["succeeded"]


@pytest.mark.asyncio
async def test_retryable_503_first_attempt() -> None:
    """503 on attempt 1 → failed, re-enqueued with 30s, metric failed."""
    ep = _endpoint()
    req = _captured(ep.id)
    fwd = _forward(ep.id, req.id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    target.respond(status=503, body=b"", headers={})
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    outcome = await fwd_repo.find_by_id(fwd.id)
    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.next_attempt_at is not None
    assert len(queue.enqueued) == 1
    assert queue.enqueued[0] == (fwd.id, 30)
    assert metrics.forward_attempt_calls == ["failed"]


@pytest.mark.asyncio
async def test_network_error_first_attempt() -> None:
    """ConnectError on attempt 1 → failed + re-enqueued with 30s."""
    ep = _endpoint()
    req = _captured(ep.id)
    fwd = _forward(ep.id, req.id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    target.raise_on_send(HttpRequestFailedError("ConnectError: connection refused", kind="network"))
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    outcome = await fwd_repo.find_by_id(fwd.id)
    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.final_error is not None
    assert "ConnectError" in outcome.final_error
    assert len(queue.enqueued) == 1
    assert queue.enqueued[0][1] == 30


@pytest.mark.asyncio
async def test_5th_attempt_503_exhausts_budget() -> None:
    """503 on the 5th attempt → dead (budget exhausted), no re-enqueue."""
    ep = _endpoint()
    req = _captured(ep.id)
    # Simulate a forward that already did 4 attempts (so attempt_count=4 after claim)
    fwd = _forward(ep.id, req.id, attempt_count=4, status="failed")

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    target.respond(status=503, body=b"", headers={})
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    outcome = await fwd_repo.find_by_id(fwd.id)
    assert outcome is not None
    assert outcome.status == "dead"
    assert queue.enqueued == []
    assert metrics.forward_attempt_calls == ["dead"]


@pytest.mark.asyncio
async def test_4xx_hard_fail_is_immediately_dead() -> None:
    """404 → dead immediately (non-retryable), no re-enqueue."""
    ep = _endpoint()
    req = _captured(ep.id)
    fwd = _forward(ep.id, req.id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    target.respond(status=404, body=b"not found", headers={})
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    outcome = await fwd_repo.find_by_id(fwd.id)
    assert outcome is not None
    assert outcome.status == "dead"
    assert outcome.final_status_code == 404
    assert queue.enqueued == []
    assert metrics.forward_attempt_calls == ["dead"]


@pytest.mark.asyncio
async def test_ssrf_block_records_dead_with_error_message() -> None:
    """SSRF block → dead with 'SsrfBlockedError: ...' error, no re-enqueue."""
    ep = _endpoint()
    req = _captured(ep.id)
    fwd = _forward(ep.id, req.id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    target.raise_on_validate(SsrfBlockedError("host resolves to private IP"))
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    outcome = await fwd_repo.find_by_id(fwd.id)
    assert outcome is not None
    assert outcome.status == "dead"
    assert outcome.final_error is not None
    assert "SsrfBlockedError" in outcome.final_error
    assert "private IP" in outcome.final_error
    assert queue.enqueued == []
    assert metrics.forward_attempt_calls == ["ssrf_blocked"]


@pytest.mark.asyncio
async def test_claim_fails_duplicate_fire_is_skipped() -> None:
    """If claim_for_attempt returns None (already in_flight), emit skipped metric."""
    ep = _endpoint()
    req = _captured(ep.id)
    # Forward already in_flight — cannot be claimed
    fwd = _forward(ep.id, req.id, attempt_count=1, status="in_flight")

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    # No outcome recorded (claim returned None → returned early)
    assert metrics.forward_attempt_calls == ["skipped"]
    assert queue.enqueued == []
    # target was never called
    assert target.last_call is None


@pytest.mark.asyncio
async def test_endpoint_deleted_ttl_records_dead() -> None:
    """Endpoint not found (TTL) → dead with descriptive error, no re-enqueue."""
    req = _captured(uuid4())
    ep_id = uuid4()
    fwd = _forward(ep_id, req.id)

    # endpoint_repo has no endpoint
    ep_repo = FakeEndpointRepo()
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    outcome = await fwd_repo.find_by_id(fwd.id)
    assert outcome is not None
    assert outcome.status == "dead"
    assert outcome.final_error is not None
    assert "TTL" in outcome.final_error or "not found" in outcome.final_error
    assert queue.enqueued == []
    assert metrics.forward_attempt_calls == ["dead"]


@pytest.mark.asyncio
async def test_request_deleted_ttl_records_dead() -> None:
    """Request not found (TTL) → dead, no re-enqueue."""
    ep = _endpoint()
    req_id = uuid4()
    fwd = _forward(ep.id, req_id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo()  # empty — request not found
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()
    target = FakeHttpReplayTarget()
    metrics = FakeMetricsCollector()

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        forward_queue=queue,
        target=target,
        metrics=metrics,
    )
    await uc.execute(forward_id=fwd.id)

    outcome = await fwd_repo.find_by_id(fwd.id)
    assert outcome is not None
    assert outcome.status == "dead"
    assert queue.enqueued == []
    assert metrics.forward_attempt_calls == ["dead"]


@pytest.mark.asyncio
async def test_hmac_signature_applied_when_endpoint_has_forward_secret() -> None:
    """X-Hooktrace-Signature header must be present when forward_secret_encrypted set."""
    encrypted = encrypt_secret(_SECRETS_KEY, "my_webhook_secret")
    ep = _endpoint(forward_secret_encrypted=encrypted)
    req = _captured(ep.id)
    fwd = _forward(ep.id, req.id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    target = FakeHttpReplayTarget()
    target.respond(status=200, body=b"ok", headers={})

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        target=target,
        secrets_key=_SECRETS_KEY,
    )
    await uc.execute(forward_id=fwd.id)

    assert target.last_call is not None
    assert "X-Hooktrace-Signature" in target.last_call.headers
    sig_header = target.last_call.headers["X-Hooktrace-Signature"]
    # Must be in Stripe-style format: "t=<ts>,v1=<hex>"
    assert sig_header.startswith("t=")
    assert ",v1=" in sig_header


@pytest.mark.asyncio
async def test_body_fetched_from_r2_when_blob_key_set() -> None:
    """When blob_key is set, body must be fetched from blob_storage."""
    ep = _endpoint()
    blob_key = "blob/abc123"
    r2_body = b'{"data":"from_r2"}'
    req = _captured(ep.id, body_preview="inline ignored", blob_key=blob_key)
    fwd = _forward(ep.id, req.id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    blob = FakeBlobStorage(blobs={blob_key: r2_body})
    target = FakeHttpReplayTarget()
    target.respond(status=200, body=b"ok", headers={})

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        target=target,
        blob_storage=blob,
    )
    await uc.execute(forward_id=fwd.id)

    assert target.last_call is not None
    assert target.last_call.body == r2_body


@pytest.mark.asyncio
async def test_header_fusion_authorization_stripped_endpoint_wins() -> None:
    """Captured Authorization is stripped; endpoint.forward_headers Authorization replaces it."""
    captured_headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer captured_token",
        "X-Custom": "keep-me",
    }
    endpoint_headers = {"Authorization": "Bearer endpoint_token"}
    ep = _endpoint(forward_headers=endpoint_headers)
    req = _captured(ep.id, headers=captured_headers)
    fwd = _forward(ep.id, req.id)

    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    target = FakeHttpReplayTarget()
    target.respond(status=200, body=b"ok", headers={})

    uc = _use_case(
        endpoint_repo=ep_repo,
        request_repo=req_repo,
        forward_repo=fwd_repo,
        target=target,
    )
    await uc.execute(forward_id=fwd.id)

    assert target.last_call is not None
    headers_sent = target.last_call.headers
    # Endpoint Authorization wins
    assert headers_sent.get("Authorization") == "Bearer endpoint_token"
    # Custom header passes through
    assert headers_sent.get("X-Custom") == "keep-me"
    # Idempotency-Key and X-Hooktrace-Forward-Id always present
    assert "Idempotency-Key" in headers_sent
    assert "X-Hooktrace-Forward-Id" in headers_sent
