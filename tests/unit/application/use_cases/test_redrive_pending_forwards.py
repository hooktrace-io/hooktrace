"""Unit tests for RedrivePendingForwards use case.

Covers: happy path (N stuck IDs → N enqueue calls → returns N),
no stuck IDs → returns 0 + no enqueue, queue raises mid-loop continues
processing later IDs and returns the original eligible count, unknown
endpoint → EndpointNotFoundError.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tests.fakes import FakeEndpointRepo, FakeForwardQueue, FakeForwardRepository
from webhook_inspector.application.use_cases.redrive_pending_forwards import (
    STUCK_PENDING_THRESHOLD_SECONDS,
    RedrivePendingForwards,
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


def _stuck_forward(endpoint_id) -> Forward:
    """Create a 'pending' forward with created_at older than the stuck threshold."""
    now = datetime.now(UTC)
    old = now - timedelta(seconds=STUCK_PENDING_THRESHOLD_SECONDS + 10)
    return Forward(
        id=uuid4(),
        request_id=uuid4(),
        endpoint_id=endpoint_id,
        target_url="https://example.com/hook",
        status="pending",
        attempt_count=0,
        last_attempt_at=None,
        next_attempt_at=old,
        final_status_code=None,
        final_error=None,
        forward_started_at=None,
        forward_completed_at=None,
        created_at=old,
        manual_retry_at=None,
    )


async def test_enqueues_each_stuck_id_and_returns_count():
    ep = _endpoint()
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    stuck = [_stuck_forward(ep.id) for _ in range(3)]
    for f in stuck:
        await fwd_repo.save(f)
    queue = FakeForwardQueue()

    uc = RedrivePendingForwards(endpoint_repo=ep_repo, forward_repo=fwd_repo, forward_queue=queue)

    count = await uc.execute(token=ep.token)

    assert count == 3
    assert len(queue.enqueued) == 3
    enqueued_ids = {fid for fid, _defer in queue.enqueued}
    assert enqueued_ids == {f.id for f in stuck}
    assert all(defer == 0 for _fid, defer in queue.enqueued)


async def test_no_stuck_forwards_returns_zero_no_enqueue():
    ep = _endpoint()
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    queue = FakeForwardQueue()

    uc = RedrivePendingForwards(endpoint_repo=ep_repo, forward_repo=fwd_repo, forward_queue=queue)

    count = await uc.execute(token=ep.token)

    assert count == 0
    assert queue.enqueued == []


async def test_continues_through_enqueue_failure_returns_total_count():
    """If the queue raises mid-loop, the use case must keep enqueueing the
    rest (so partial progress is made) and still return len(stuck_ids).
    """
    ep = _endpoint()
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    stuck = [_stuck_forward(ep.id) for _ in range(3)]
    for f in stuck:
        await fwd_repo.save(f)

    # FakeForwardQueue subclass that fails the FIRST enqueue call only.
    class FlakyQueue(FakeForwardQueue):
        def __init__(self) -> None:
            super().__init__()
            self._calls = 0

        async def enqueue(self, forward_id: UUID, *, defer_seconds: int = 0) -> None:
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("redis unavailable")
            await super().enqueue(forward_id, defer_seconds=defer_seconds)

    queue = FlakyQueue()

    uc = RedrivePendingForwards(endpoint_repo=ep_repo, forward_repo=fwd_repo, forward_queue=queue)

    count = await uc.execute(token=ep.token)

    # Count is the number of eligible rows, not the number of successful enqueues.
    assert count == 3
    # 2 of 3 enqueues succeeded; the loop continued past the failure.
    assert len(queue.enqueued) == 2


async def test_raises_endpoint_not_found_for_unknown_token():
    uc = RedrivePendingForwards(
        endpoint_repo=FakeEndpointRepo(),
        forward_repo=FakeForwardRepository(),
        forward_queue=FakeForwardQueue(),
    )

    with pytest.raises(EndpointNotFoundError):
        await uc.execute(token="missing")


def test_threshold_constant_is_five_minutes():
    assert STUCK_PENDING_THRESHOLD_SECONDS == 5 * 60
