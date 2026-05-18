"""Integration tests for PostgresForwardRepository — exercises real Postgres
schema (migration 0011) via testcontainers.

Covers the 5 DLQ-ops methods added in PR7+PR8 Block 5:
- list_by_endpoint (cursor pagination + status filters)
- count_by_status (all 6 statuses always present)
- claim_for_manual_retry (state transitions, attempt_count reset rules)
- abandon (rejects already-terminal rows)
- redrive_stuck_pending (returns IDs only, doesn't mutate)
"""

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.entities.forward import MAX_ATTEMPTS, Forward, ForwardStatus
from webhook_inspector.infrastructure.repositories.endpoint_repository import (
    PostgresEndpointRepository,
)
from webhook_inspector.infrastructure.repositories.forward_repository import (
    PostgresForwardRepository,
)
from webhook_inspector.infrastructure.repositories.request_repository import (
    PostgresRequestRepository,
)

TARGET_URL = "https://example.com/webhook"


async def _seed_endpoint(session) -> Endpoint:
    repo = PostgresEndpointRepository(session)
    endpoint = Endpoint.create(token=secrets.token_hex(8), ttl_days=7)
    await repo.save(endpoint)
    await session.commit()
    return endpoint


async def _seed_request(session, endpoint_id) -> CapturedRequest:
    repo = PostgresRequestRepository(session)
    req = CapturedRequest.create(
        endpoint_id=endpoint_id,
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"x",
        source_ip="127.0.0.1",
        inline_threshold_bytes=8192,
    )
    await repo.save(req)
    await session.commit()
    return req


async def _seed_forward(
    session,
    *,
    endpoint_id,
    request_id,
    status: ForwardStatus = "pending",
    attempt_count: int = 0,
    created_at: datetime | None = None,
    final_status_code: int | None = None,
    final_error: str | None = None,
    forward_completed_at: datetime | None = None,
) -> Forward:
    repo = PostgresForwardRepository(session)
    now = datetime.now(UTC)
    forward = Forward.create(
        request_id=request_id,
        endpoint_id=endpoint_id,
        target_url=TARGET_URL,
        now=now,
    )
    forward = replace(
        forward,
        status=status,
        attempt_count=attempt_count,
        created_at=created_at or now,
        final_status_code=final_status_code,
        final_error=final_error,
        forward_completed_at=forward_completed_at,
    )
    await repo.save(forward)
    await session.commit()
    return forward


# ---- list_by_endpoint ------------------------------------------------------


async def test_list_by_endpoint_no_filter_returns_all_newest_first(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    now = datetime.now(UTC)
    ids = []
    for i in range(3):
        f = await _seed_forward(
            session,
            endpoint_id=endpoint.id,
            request_id=req.id,
            status="failed",
            created_at=now - timedelta(seconds=10 - i),  # i=0 oldest, i=2 newest
        )
        ids.append(f.id)

    result = await repo.list_by_endpoint(endpoint.id)
    assert [f.id for f in result] == list(reversed(ids))  # newest first


async def test_list_by_endpoint_filters_by_single_status(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    pending = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="pending"
    )
    await _seed_forward(session, endpoint_id=endpoint.id, request_id=req.id, status="succeeded")
    await _seed_forward(session, endpoint_id=endpoint.id, request_id=req.id, status="failed")

    result = await repo.list_by_endpoint(endpoint.id, statuses=["pending"])
    assert len(result) == 1
    assert result[0].id == pending.id


async def test_list_by_endpoint_filters_by_multi_status(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    dead = await _seed_forward(session, endpoint_id=endpoint.id, request_id=req.id, status="dead")
    abandoned = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="abandoned"
    )
    await _seed_forward(session, endpoint_id=endpoint.id, request_id=req.id, status="succeeded")

    result = await repo.list_by_endpoint(endpoint.id, statuses=["dead", "abandoned"])
    ids = {f.id for f in result}
    assert ids == {dead.id, abandoned.id}


async def test_list_by_endpoint_cursor_pagination_across_three_rows(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    now = datetime.now(UTC)
    ids = []
    for i in range(3):
        f = await _seed_forward(
            session,
            endpoint_id=endpoint.id,
            request_id=req.id,
            status="failed",
            created_at=now - timedelta(seconds=10 - i),
        )
        ids.append(f.id)
    # Newest-first ordering: ids[2], ids[1], ids[0]

    page1 = await repo.list_by_endpoint(endpoint.id, limit=2)
    assert [f.id for f in page1] == [ids[2], ids[1]]

    page2 = await repo.list_by_endpoint(endpoint.id, limit=2, before_id=page1[-1].id)
    assert [f.id for f in page2] == [ids[0]]


# ---- count_by_status -------------------------------------------------------


async def test_count_by_status_returns_all_six_statuses(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    await _seed_forward(session, endpoint_id=endpoint.id, request_id=req.id, status="pending")
    await _seed_forward(session, endpoint_id=endpoint.id, request_id=req.id, status="pending")
    await _seed_forward(session, endpoint_id=endpoint.id, request_id=req.id, status="failed")

    counts = await repo.count_by_status(endpoint.id)
    # All 6 statuses must be present, even those with no rows.
    assert set(counts.keys()) == {
        "pending",
        "in_flight",
        "succeeded",
        "failed",
        "dead",
        "abandoned",
    }
    assert counts["pending"] == 2
    assert counts["failed"] == 1
    assert counts["succeeded"] == 0
    assert counts["in_flight"] == 0
    assert counts["dead"] == 0
    assert counts["abandoned"] == 0


# ---- claim_for_manual_retry ------------------------------------------------


async def test_claim_for_manual_retry_from_failed_keeps_attempt_count(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session,
        endpoint_id=endpoint.id,
        request_id=req.id,
        status="failed",
        attempt_count=2,
        final_status_code=500,
        final_error="boom",
    )

    now = datetime.now(UTC)
    claimed = await repo.claim_for_manual_retry(forward.id, endpoint.id, now=now)
    await session.commit()

    assert claimed is not None
    assert claimed.status == "pending"
    assert claimed.attempt_count == 2  # unchanged from 'failed'
    assert claimed.manual_retry_at == now
    assert claimed.next_attempt_at == now
    assert claimed.final_error is None
    assert claimed.final_status_code is None


async def test_claim_for_manual_retry_from_dead_resets_attempt_count(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session,
        endpoint_id=endpoint.id,
        request_id=req.id,
        status="dead",
        attempt_count=MAX_ATTEMPTS,
        final_status_code=500,
        final_error="too many tries",
        forward_completed_at=datetime.now(UTC),
    )

    now = datetime.now(UTC)
    claimed = await repo.claim_for_manual_retry(forward.id, endpoint.id, now=now)
    await session.commit()

    assert claimed is not None
    assert claimed.status == "pending"
    # MAX_ATTEMPTS - 1 = 4 — gives the worker one more shot before dead again.
    assert claimed.attempt_count == max(0, MAX_ATTEMPTS - 1)
    assert claimed.manual_retry_at == now
    assert claimed.final_error is None
    assert claimed.final_status_code is None


async def test_claim_for_manual_retry_from_abandoned_resets_attempt_count(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session,
        endpoint_id=endpoint.id,
        request_id=req.id,
        status="abandoned",
        attempt_count=3,
        final_error="manually abandoned by owner",
        forward_completed_at=datetime.now(UTC),
    )

    now = datetime.now(UTC)
    claimed = await repo.claim_for_manual_retry(forward.id, endpoint.id, now=now)
    await session.commit()

    assert claimed is not None
    assert claimed.status == "pending"
    assert claimed.attempt_count == max(0, MAX_ATTEMPTS - 1)
    assert claimed.final_error is None


async def test_claim_for_manual_retry_rejects_in_flight(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="in_flight"
    )

    claimed = await repo.claim_for_manual_retry(forward.id, endpoint.id, now=datetime.now(UTC))
    assert claimed is None


async def test_claim_for_manual_retry_rejects_succeeded(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="succeeded"
    )

    claimed = await repo.claim_for_manual_retry(forward.id, endpoint.id, now=datetime.now(UTC))
    assert claimed is None


async def test_claim_for_manual_retry_rejects_on_endpoint_mismatch(session):
    endpoint_a = await _seed_endpoint(session)
    endpoint_b = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint_a.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint_a.id, request_id=req.id, status="failed"
    )

    # Pretend endpoint_b's owner is trying to retry endpoint_a's forward.
    claimed = await repo.claim_for_manual_retry(forward.id, endpoint_b.id, now=datetime.now(UTC))
    assert claimed is None


# ---- abandon ---------------------------------------------------------------


async def test_abandon_transitions_pending_to_abandoned(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="pending"
    )

    now = datetime.now(UTC)
    abandoned = await repo.abandon(forward.id, endpoint.id, now=now)
    await session.commit()

    assert abandoned is not None
    assert abandoned.status == "abandoned"
    assert abandoned.forward_completed_at == now
    assert abandoned.final_error == "manually abandoned by owner"


async def test_abandon_transitions_failed_to_abandoned(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="failed"
    )

    abandoned = await repo.abandon(forward.id, endpoint.id, now=datetime.now(UTC))
    assert abandoned is not None
    assert abandoned.status == "abandoned"


async def test_abandon_rejects_succeeded(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="succeeded"
    )

    result = await repo.abandon(forward.id, endpoint.id, now=datetime.now(UTC))
    assert result is None


async def test_abandon_rejects_dead(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="dead"
    )

    result = await repo.abandon(forward.id, endpoint.id, now=datetime.now(UTC))
    assert result is None


async def test_abandon_rejects_already_abandoned(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint.id, request_id=req.id, status="abandoned"
    )

    result = await repo.abandon(forward.id, endpoint.id, now=datetime.now(UTC))
    assert result is None


async def test_abandon_rejects_on_endpoint_mismatch(session):
    endpoint_a = await _seed_endpoint(session)
    endpoint_b = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint_a.id)
    repo = PostgresForwardRepository(session)

    forward = await _seed_forward(
        session, endpoint_id=endpoint_a.id, request_id=req.id, status="pending"
    )

    result = await repo.abandon(forward.id, endpoint_b.id, now=datetime.now(UTC))
    assert result is None


# ---- redrive_stuck_pending -------------------------------------------------


async def test_redrive_stuck_pending_returns_only_old_pending(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    now = datetime.now(UTC)
    threshold = 300  # 5 min

    stuck = await _seed_forward(
        session,
        endpoint_id=endpoint.id,
        request_id=req.id,
        status="pending",
        created_at=now - timedelta(seconds=threshold + 60),
    )
    # Fresh — within the 5-min window, must be excluded.
    await _seed_forward(
        session,
        endpoint_id=endpoint.id,
        request_id=req.id,
        status="pending",
        created_at=now - timedelta(seconds=30),
    )
    # Old, but not 'pending' — must be excluded.
    await _seed_forward(
        session,
        endpoint_id=endpoint.id,
        request_id=req.id,
        status="failed",
        created_at=now - timedelta(seconds=threshold + 60),
    )

    ids = await repo.redrive_stuck_pending(endpoint.id, stuck_threshold_seconds=threshold, now=now)
    assert ids == [stuck.id]


async def test_redrive_stuck_pending_does_not_mutate_rows(session):
    endpoint = await _seed_endpoint(session)
    req = await _seed_request(session, endpoint.id)
    repo = PostgresForwardRepository(session)

    now = datetime.now(UTC)
    forward = await _seed_forward(
        session,
        endpoint_id=endpoint.id,
        request_id=req.id,
        status="pending",
        created_at=now - timedelta(seconds=600),
    )

    await repo.redrive_stuck_pending(endpoint.id, stuck_threshold_seconds=300, now=now)
    await session.commit()

    # Status MUST be unchanged.
    reloaded = await repo.find_by_id(forward.id)
    assert reloaded is not None
    assert reloaded.status == "pending"
    assert reloaded.attempt_count == 0


async def test_redrive_stuck_pending_isolates_across_endpoints(session):
    endpoint_a = await _seed_endpoint(session)
    endpoint_b = await _seed_endpoint(session)
    req_a = await _seed_request(session, endpoint_a.id)
    req_b = await _seed_request(session, endpoint_b.id)
    repo = PostgresForwardRepository(session)

    now = datetime.now(UTC)
    a_stuck = await _seed_forward(
        session,
        endpoint_id=endpoint_a.id,
        request_id=req_a.id,
        status="pending",
        created_at=now - timedelta(seconds=600),
    )
    await _seed_forward(
        session,
        endpoint_id=endpoint_b.id,
        request_id=req_b.id,
        status="pending",
        created_at=now - timedelta(seconds=600),
    )

    a_ids = await repo.redrive_stuck_pending(endpoint_a.id, stuck_threshold_seconds=300, now=now)
    assert a_ids == [a_stuck.id]


async def test_redrive_stuck_pending_returns_empty_when_none(session):
    endpoint = await _seed_endpoint(session)
    repo = PostgresForwardRepository(session)
    ids = await repo.redrive_stuck_pending(
        endpoint.id, stuck_threshold_seconds=300, now=datetime.now(UTC)
    )
    assert ids == []


async def test_list_by_endpoint_isolates_across_endpoints(session):
    endpoint_a = await _seed_endpoint(session)
    endpoint_b = await _seed_endpoint(session)
    req_a = await _seed_request(session, endpoint_a.id)
    req_b = await _seed_request(session, endpoint_b.id)
    repo = PostgresForwardRepository(session)

    a_forward = await _seed_forward(
        session, endpoint_id=endpoint_a.id, request_id=req_a.id, status="failed"
    )
    await _seed_forward(session, endpoint_id=endpoint_b.id, request_id=req_b.id, status="failed")

    a_rows = await repo.list_by_endpoint(endpoint_a.id)
    assert len(a_rows) == 1
    assert a_rows[0].id == a_forward.id


async def test_count_by_status_isolates_across_endpoints(session):
    endpoint_a = await _seed_endpoint(session)
    endpoint_b = await _seed_endpoint(session)
    req_a = await _seed_request(session, endpoint_a.id)
    req_b = await _seed_request(session, endpoint_b.id)
    repo = PostgresForwardRepository(session)

    await _seed_forward(session, endpoint_id=endpoint_a.id, request_id=req_a.id, status="failed")
    await _seed_forward(session, endpoint_id=endpoint_b.id, request_id=req_b.id, status="failed")

    a_counts = await repo.count_by_status(endpoint_a.id)
    assert a_counts["failed"] == 1


async def test_claim_for_manual_retry_returns_none_for_unknown_forward(session):
    endpoint = await _seed_endpoint(session)
    repo = PostgresForwardRepository(session)

    result = await repo.claim_for_manual_retry(uuid4(), endpoint.id, now=datetime.now(UTC))
    assert result is None
