"""Unit tests for RetryForward use case.

Covers: happy path (claim succeeds → enqueue called → returns claimed),
not-claimable path (raises ForwardNotRetryableError AND does NOT enqueue),
unknown endpoint → EndpointNotFoundError.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.fakes import FakeEndpointRepo, FakeForwardQueue, FakeForwardRepository
from webhook_inspector.application.use_cases.retry_forward import (
    ForwardNotRetryableError,
    RetryForward,
)
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.exceptions import EndpointNotFoundError


def _endpoint(token: str = "tok-abc") -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token=token,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
    )


def _forward(endpoint_id, *, status: str = "failed") -> Forward:
    now = datetime.now(UTC)
    return Forward(
        id=uuid4(),
        request_id=uuid4(),
        endpoint_id=endpoint_id,
        target_url="https://example.com/hook",
        status=status,  # type: ignore[arg-type]
        attempt_count=2,
        last_attempt_at=now,
        next_attempt_at=None,
        final_status_code=500,
        final_error="boom",
        forward_started_at=now,
        forward_completed_at=None,
        created_at=now,
        manual_retry_at=None,
    )


async def test_claims_then_enqueues_and_returns_claimed():
    ep = _endpoint()
    fwd = _forward(ep.id, status="failed")
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()

    uc = RetryForward(endpoint_repo=ep_repo, forward_repo=fwd_repo, forward_queue=queue)

    result = await uc.execute(token=ep.token, forward_id=fwd.id)

    assert result.id == fwd.id
    assert result.status == "pending"
    assert queue.enqueued == [(fwd.id, 0)]


async def test_succeeded_forward_is_not_retryable_and_not_enqueued():
    ep = _endpoint()
    fwd = _forward(ep.id, status="succeeded")
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()

    uc = RetryForward(endpoint_repo=ep_repo, forward_repo=fwd_repo, forward_queue=queue)

    with pytest.raises(ForwardNotRetryableError):
        await uc.execute(token=ep.token, forward_id=fwd.id)

    # Critical: queue must NOT have been called when claim returns None.
    assert queue.enqueued == []


async def test_cross_endpoint_forward_is_not_retryable():
    ep_owner = _endpoint("tok-owner")
    ep_other = _endpoint("tok-other")
    fwd = _forward(ep_other.id, status="failed")  # belongs to other ep
    ep_repo = FakeEndpointRepo(seed=ep_owner)
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(fwd)
    queue = FakeForwardQueue()

    uc = RetryForward(endpoint_repo=ep_repo, forward_repo=fwd_repo, forward_queue=queue)

    with pytest.raises(ForwardNotRetryableError):
        await uc.execute(token=ep_owner.token, forward_id=fwd.id)

    assert queue.enqueued == []


async def test_raises_endpoint_not_found_for_unknown_token():
    uc = RetryForward(
        endpoint_repo=FakeEndpointRepo(),
        forward_repo=FakeForwardRepository(),
        forward_queue=FakeForwardQueue(),
    )

    with pytest.raises(EndpointNotFoundError):
        await uc.execute(token="missing", forward_id=uuid4())
