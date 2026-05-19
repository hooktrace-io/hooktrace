"""Integration tests for the DLQ routes under /api/endpoints/{token}/forwards.

Seeding strategy: build a real Endpoint via POST /api/endpoints, capture one
request via the ingestor (so the forwards FK to requests is satisfied), then
insert Forward rows directly via PostgresForwardRepository with whatever
status the test needs. The queue is monkeypatched to a FakeForwardQueue so
enqueue calls can be asserted without Redis.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from tests.fakes.forward_queue import FakeForwardQueue
from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.infrastructure.repositories.forward_repository import (
    PostgresForwardRepository,
)


async def _create_endpoint(app_client) -> str:
    resp = await app_client.post("/api/endpoints", json={})
    assert resp.status_code == 201
    return resp.json()["token"]


async def _capture_request(ingestor_client, token: str, body: bytes = b"x") -> None:
    resp = await ingestor_client.post(f"/h/{token}", content=body)
    assert resp.status_code == 200


async def _endpoint_id_and_request_id(session, token: str) -> tuple[UUID, UUID]:
    ep_row = (
        await session.execute(text("SELECT id FROM endpoints WHERE token = :t"), {"t": token})
    ).one()
    req_row = (
        await session.execute(
            text("SELECT id FROM requests WHERE endpoint_id = :e LIMIT 1"),
            {"e": ep_row.id},
        )
    ).one()
    return ep_row.id, req_row.id


def _make_forward(
    *,
    endpoint_id: UUID,
    request_id: UUID,
    status: str,
    attempt_count: int = 0,
    created_at: datetime | None = None,
    target_url: str = "https://example.com/wh",
    final_status_code: int | None = None,
    final_error: str | None = None,
) -> Forward:
    now = created_at or datetime.now(UTC)
    return Forward(
        id=uuid4(),
        request_id=request_id,
        endpoint_id=endpoint_id,
        target_url=target_url,
        status=status,  # type: ignore[arg-type]
        attempt_count=attempt_count,
        last_attempt_at=now if attempt_count > 0 else None,
        next_attempt_at=None,
        final_status_code=final_status_code,
        final_error=final_error,
        forward_started_at=None,
        forward_completed_at=None,
        created_at=now,
        manual_retry_at=None,
    )


@pytest.fixture
def fake_queue(monkeypatch):
    """Monkeypatch get_forward_queue to return a shared FakeForwardQueue."""
    queue = FakeForwardQueue()
    monkeypatch.setattr(
        "webhook_inspector.web.app.deps.get_forward_queue",
        lambda: queue,
    )
    return queue


async def _seed_forward(session_factory, forward: Forward) -> None:
    async with session_factory() as s:
        repo = PostgresForwardRepository(s)
        await repo.save(forward)
        await s.commit()


@pytest.mark.asyncio
async def test_list_filters_by_status(app_client, ingestor_client, session_factory):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    # The capture also inserted a 'pending' forward indirectly? No — the endpoint
    # has no forward_url configured, so no forward was created automatically.
    await _seed_forward(
        session_factory, _make_forward(endpoint_id=ep_id, request_id=req_id, status="succeeded")
    )
    await _seed_forward(
        session_factory, _make_forward(endpoint_id=ep_id, request_id=req_id, status="failed")
    )
    await _seed_forward(
        session_factory, _make_forward(endpoint_id=ep_id, request_id=req_id, status="dead")
    )

    resp = await app_client.get(f"/api/endpoints/{token}/forwards?status=dead")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "dead"


@pytest.mark.asyncio
async def test_list_filters_by_multiple_statuses(app_client, ingestor_client, session_factory):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    for st in ("succeeded", "failed", "dead", "abandoned"):
        await _seed_forward(
            session_factory, _make_forward(endpoint_id=ep_id, request_id=req_id, status=st)
        )

    resp = await app_client.get(f"/api/endpoints/{token}/forwards?status=failed&status=dead")
    assert resp.status_code == 200
    statuses = sorted(i["status"] for i in resp.json()["items"])
    assert statuses == ["dead", "failed"]


@pytest.mark.asyncio
async def test_list_no_status_filter_returns_all(app_client, ingestor_client, session_factory):
    """The API route is filter-less by default; the HTML route applies the default filter."""
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    for st in ("succeeded", "abandoned", "failed"):
        await _seed_forward(
            session_factory, _make_forward(endpoint_id=ep_id, request_id=req_id, status=st)
        )

    resp = await app_client.get(f"/api/endpoints/{token}/forwards")
    assert resp.status_code == 200
    statuses = sorted(i["status"] for i in resp.json()["items"])
    assert statuses == ["abandoned", "failed", "succeeded"]


@pytest.mark.asyncio
async def test_retry_dead_forward_resets_attempt_count(
    app_client, ingestor_client, session_factory, fake_queue
):
    from webhook_inspector.domain.entities.forward import MAX_ATTEMPTS

    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    fwd = _make_forward(
        endpoint_id=ep_id,
        request_id=req_id,
        status="dead",
        attempt_count=5,
        final_status_code=500,
        final_error="boom",
    )
    await _seed_forward(session_factory, fwd)

    resp = await app_client.post(f"/api/endpoints/{token}/forwards/{fwd.id}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["attempt_count"] == max(0, MAX_ATTEMPTS - 1)
    assert (fwd.id, 0) in fake_queue.enqueued


@pytest.mark.asyncio
async def test_retry_failed_forward_keeps_attempt_count(
    app_client, ingestor_client, session_factory, fake_queue
):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    fwd = _make_forward(endpoint_id=ep_id, request_id=req_id, status="failed", attempt_count=2)
    await _seed_forward(session_factory, fwd)

    resp = await app_client.post(f"/api/endpoints/{token}/forwards/{fwd.id}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["attempt_count"] == 2
    assert (fwd.id, 0) in fake_queue.enqueued


@pytest.mark.asyncio
async def test_retry_succeeded_forward_returns_404(
    app_client, ingestor_client, session_factory, fake_queue
):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    fwd = _make_forward(endpoint_id=ep_id, request_id=req_id, status="succeeded")
    await _seed_forward(session_factory, fwd)

    resp = await app_client.post(f"/api/endpoints/{token}/forwards/{fwd.id}/retry")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "forward not retryable"}
    # Critical: must NOT have been enqueued
    assert fake_queue.enqueued == []


@pytest.mark.asyncio
async def test_retry_in_flight_forward_returns_404(
    app_client, ingestor_client, session_factory, fake_queue
):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    fwd = _make_forward(endpoint_id=ep_id, request_id=req_id, status="in_flight")
    await _seed_forward(session_factory, fwd)

    resp = await app_client.post(f"/api/endpoints/{token}/forwards/{fwd.id}/retry")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "forward not retryable"}
    assert fake_queue.enqueued == []


@pytest.mark.asyncio
async def test_retry_cross_endpoint_returns_404(
    app_client, ingestor_client, session_factory, fake_queue
):
    """No-leak property: same body for wrong-status AND wrong-endpoint."""
    token_a = await _create_endpoint(app_client)
    token_b = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token_b)
    async with session_factory() as s:
        ep_id_b, req_id_b = await _endpoint_id_and_request_id(s, token_b)

    fwd = _make_forward(endpoint_id=ep_id_b, request_id=req_id_b, status="failed")
    await _seed_forward(session_factory, fwd)

    resp = await app_client.post(f"/api/endpoints/{token_a}/forwards/{fwd.id}/retry")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "forward not retryable"}
    assert fake_queue.enqueued == []


@pytest.mark.asyncio
async def test_abandon_dead_forward_returns_404(app_client, ingestor_client, session_factory):
    """Already-terminal rows cannot be abandoned again."""
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    fwd = _make_forward(endpoint_id=ep_id, request_id=req_id, status="dead")
    await _seed_forward(session_factory, fwd)

    resp = await app_client.delete(f"/api/endpoints/{token}/forwards/{fwd.id}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "forward not found"}


@pytest.mark.asyncio
async def test_abandon_failed_forward_soft_deletes(app_client, ingestor_client, session_factory):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    fwd = _make_forward(endpoint_id=ep_id, request_id=req_id, status="failed")
    await _seed_forward(session_factory, fwd)

    resp = await app_client.delete(f"/api/endpoints/{token}/forwards/{fwd.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "abandoned"
    assert body["final_error"] == "manually abandoned by owner"

    # Row still exists in DB and has forward_completed_at set
    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, forward_completed_at, final_error FROM forwards WHERE id = :id"
                ),
                {"id": fwd.id},
            )
        ).one()
    assert row.status == "abandoned"
    assert row.forward_completed_at is not None
    assert row.final_error == "manually abandoned by owner"


@pytest.mark.asyncio
async def test_abandon_cross_endpoint_returns_404(app_client, ingestor_client, session_factory):
    token_a = await _create_endpoint(app_client)
    token_b = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token_b)
    async with session_factory() as s:
        ep_id_b, req_id_b = await _endpoint_id_and_request_id(s, token_b)

    fwd = _make_forward(endpoint_id=ep_id_b, request_id=req_id_b, status="failed")
    await _seed_forward(session_factory, fwd)

    resp = await app_client.delete(f"/api/endpoints/{token_a}/forwards/{fwd.id}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "forward not found"}


@pytest.mark.asyncio
async def test_redrive_stuck_pending_enqueues(
    app_client, ingestor_client, session_factory, fake_queue
):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    stuck = _make_forward(
        endpoint_id=ep_id,
        request_id=req_id,
        status="pending",
        created_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    await _seed_forward(session_factory, stuck)

    resp = await app_client.post(f"/api/endpoints/{token}/forwards/redrive")
    assert resp.status_code == 200
    assert resp.json() == {"redriven": 1}
    assert (stuck.id, 0) in fake_queue.enqueued


@pytest.mark.asyncio
async def test_redrive_skips_fresh_pending(
    app_client, ingestor_client, session_factory, fake_queue
):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    fresh = _make_forward(
        endpoint_id=ep_id,
        request_id=req_id,
        status="pending",
        created_at=datetime.now(UTC) - timedelta(seconds=30),
    )
    await _seed_forward(session_factory, fresh)

    resp = await app_client.post(f"/api/endpoints/{token}/forwards/redrive")
    assert resp.status_code == 200
    assert resp.json() == {"redriven": 0}
    assert fake_queue.enqueued == []


@pytest.mark.asyncio
async def test_stats_returns_all_six_statuses(app_client, ingestor_client, session_factory):
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    await _seed_forward(
        session_factory, _make_forward(endpoint_id=ep_id, request_id=req_id, status="succeeded")
    )

    resp = await app_client.get(f"/api/endpoints/{token}/forwards/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "pending": 0,
        "in_flight": 0,
        "succeeded": 1,
        "failed": 0,
        "dead": 0,
        "abandoned": 0,
    }


@pytest.mark.asyncio
async def test_limit_outside_range_returns_400(app_client):
    token = await _create_endpoint(app_client)

    resp = await app_client.get(f"/api/endpoints/{token}/forwards?limit=0")
    assert resp.status_code == 400

    resp = await app_client.get(f"/api/endpoints/{token}/forwards?limit=201")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unknown_endpoint_returns_404_on_all_routes(app_client):
    bogus = "no-such-token-xyz"
    fwd_id = uuid4()

    resp = await app_client.get(f"/api/endpoints/{bogus}/forwards")
    assert resp.status_code == 404

    resp = await app_client.get(f"/api/endpoints/{bogus}/forwards/stats")
    assert resp.status_code == 404

    resp = await app_client.post(f"/api/endpoints/{bogus}/forwards/{fwd_id}/retry")
    assert resp.status_code == 404

    resp = await app_client.post(f"/api/endpoints/{bogus}/forwards/redrive")
    assert resp.status_code == 404

    resp = await app_client.delete(f"/api/endpoints/{bogus}/forwards/{fwd_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_html_forwards_view_renders(app_client, ingestor_client, session_factory):
    """GET /{token}/forwards renders forwards.html with stats + filtered rows."""
    token = await _create_endpoint(app_client)
    await _capture_request(ingestor_client, token)
    async with session_factory() as s:
        ep_id, req_id = await _endpoint_id_and_request_id(s, token)

    # Default filter excludes succeeded + abandoned — these should NOT appear.
    await _seed_forward(
        session_factory, _make_forward(endpoint_id=ep_id, request_id=req_id, status="succeeded")
    )
    dead_fwd = _make_forward(endpoint_id=ep_id, request_id=req_id, status="dead")
    await _seed_forward(session_factory, dead_fwd)

    resp = await app_client.get(f"/{token}/forwards")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # The dead row should be present
    assert str(dead_fwd.id) in resp.text
    # Status counters render somewhere on the page
    assert "succeeded" in resp.text
    assert "abandoned" in resp.text
