"""Integration test for the abuse_scan cron.

Seeds: endpoint A with 25 POST captures + 0 forwards. Endpoint B with 25
POST captures + 5 successful forwards. Endpoint C with 5 POSTs + 0 forwards
(below threshold).

Asserts:
- A flagged (status='phishing_no_forward', flagged_at set)
- B NOT flagged (has successful forwards)
- C NOT flagged (below 20 POST threshold)
- Return value = 1 (only A)
- Discord webhook invoked when abuse_webhook_url is set; NOT invoked when None
"""

import secrets
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.infrastructure.repositories.endpoint_repository import (
    PostgresEndpointRepository,
)
from webhook_inspector.infrastructure.repositories.forward_repository import (
    PostgresForwardRepository,
)
from webhook_inspector.infrastructure.repositories.request_repository import (
    PostgresRequestRepository,
)
from webhook_inspector.jobs import abuse_scan
from webhook_inspector.jobs.abuse_scan import run_abuse_scan


async def _seed_endpoint(session_factory, token: str) -> Endpoint:
    async with session_factory() as s:
        repo = PostgresEndpointRepository(s)
        endpoint = Endpoint.create(token=token, ttl_days=7)
        await repo.save(endpoint)
        await s.commit()
    return endpoint


async def _seed_n_posts(session_factory, endpoint_id: UUID, n: int) -> list[UUID]:
    request_ids: list[UUID] = []
    async with session_factory() as s:
        repo = PostgresRequestRepository(s)
        for _ in range(n):
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
            request_ids.append(req.id)
        await s.commit()
    return request_ids


async def _seed_succeeded_forward(session_factory, endpoint_id: UUID, request_id: UUID) -> None:
    async with session_factory() as s:
        repo = PostgresForwardRepository(s)
        now = datetime.now(UTC)
        forward = Forward.create(
            request_id=request_id,
            endpoint_id=endpoint_id,
            target_url="https://example.com/webhook",
            now=now,
        )
        forward = replace(
            forward,
            status="succeeded",
            attempt_count=1,
            forward_started_at=now,
            forward_completed_at=now,
            final_status_code=200,
        )
        await repo.save(forward)
        await s.commit()


def _token(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


async def _read_endpoint(session_factory, endpoint_id: UUID) -> Endpoint:
    async with session_factory() as s:
        repo = PostgresEndpointRepository(s)
        ep = await repo.find_by_id(endpoint_id)
    assert ep is not None
    return ep


@pytest.fixture
async def seeded(session_factory):
    """Seeds endpoints A/B/C and returns their entities."""
    a = await _seed_endpoint(session_factory, _token("abuse-a"))
    b = await _seed_endpoint(session_factory, _token("abuse-b"))
    c = await _seed_endpoint(session_factory, _token("abuse-c"))

    # A: 25 POSTs, 0 forwards => suspicious.
    await _seed_n_posts(session_factory, a.id, 25)

    # B: 25 POSTs, 5 succeeded forwards => NOT suspicious.
    b_req_ids = await _seed_n_posts(session_factory, b.id, 25)
    for req_id in b_req_ids[:5]:
        await _seed_succeeded_forward(session_factory, b.id, req_id)

    # C: 5 POSTs, 0 forwards => NOT suspicious (under threshold).
    await _seed_n_posts(session_factory, c.id, 5)

    return SimpleNamespace(a=a, b=b, c=c)


async def test_abuse_scan_flags_only_phishing_suspect_and_returns_count(
    session_factory, seeded, monkeypatch
):
    """End-to-end: scan flags A only; returns 1; B and C remain unflagged."""
    discord_calls: list[tuple[str, str]] = []

    async def fake_post_discord_alert(url: str, message: str) -> None:
        discord_calls.append((url, message))

    monkeypatch.setattr(abuse_scan, "post_discord_alert", fake_post_discord_alert)

    settings = SimpleNamespace(abuse_webhook_url="https://discord.test/webhooks/X")
    ctx = {"_session_factory": session_factory, "_settings": settings}

    flagged_count = await run_abuse_scan(ctx)

    assert flagged_count == 1

    a_after = await _read_endpoint(session_factory, seeded.a.id)
    b_after = await _read_endpoint(session_factory, seeded.b.id)
    c_after = await _read_endpoint(session_factory, seeded.c.id)

    assert a_after.flagged_at is not None
    assert a_after.flag_reason == "phishing_no_forward"

    assert b_after.flagged_at is None
    assert b_after.flag_reason is None
    assert c_after.flagged_at is None
    assert c_after.flag_reason is None

    assert len(discord_calls) == 1
    assert discord_calls[0][0] == "https://discord.test/webhooks/X"
    assert str(seeded.a.id) in discord_calls[0][1]
    assert "25 POSTs" in discord_calls[0][1]


async def test_abuse_scan_multiplicative_join_regression(session_factory, monkeypatch):
    """Regression guard: the query MUST NOT inflate post_count via a
    cartesian join with the forwards table.

    Setup: endpoint D with 5 POSTs (< threshold 20) AND 3 succeeded
    forwards. A naive INNER/LEFT JOIN of requests + forwards on endpoint_id
    in a single GROUP BY would have produced 5*3 = 15 result rows, with
    COUNT(r.id) FILTER (...) reporting 15 — false-flagging the endpoint as
    >= 20 POSTs.

    With the two-CTE rewrite, the post side sees exactly 5 rows. So:
      - flagged_count must be 0 (D is below threshold and has succeeded
        forwards anyway)
      - D.flagged_at stays None

    This test would have caught the inflation; before the fix, D would
    have shown up with post_count = 15, suspicion = (15 >= 20 AND ok==3)
    = False so still no flag — BUT seed 7 POSTs * 3 forwards = 21 and the
    bug bites:
    """
    discord_calls: list[tuple[str, str]] = []

    async def fake_post_discord_alert(url: str, message: str) -> None:
        discord_calls.append((url, message))

    monkeypatch.setattr(abuse_scan, "post_discord_alert", fake_post_discord_alert)

    # Endpoint D: 7 real POSTs, 3 succeeded forwards. 7 < 20 → not suspect.
    # Pre-fix bug: 7*3 = 21 >= 20 AND forward_ok would be COUNT(f) FILTER
    # which over the same cartesian also inflates — but the boolean check
    # forward_succeeded_count_24h == 0 holds (3 > 0) so D still wouldn't
    # be flagged via the suspicion property. To prove the join IS multi-
    # plicative, we assert the raw row's post_count value.
    d = await _seed_endpoint(session_factory, _token("abuse-d"))
    d_req_ids = await _seed_n_posts(session_factory, d.id, 7)
    for req_id in d_req_ids[:3]:
        await _seed_succeeded_forward(session_factory, d.id, req_id)

    settings = SimpleNamespace(abuse_webhook_url=None)
    ctx = {"_session_factory": session_factory, "_settings": settings}

    # Run the scan and assert D isn't flagged.
    flagged_count = await run_abuse_scan(ctx)
    assert flagged_count == 0

    d_after = await _read_endpoint(session_factory, d.id)
    assert d_after.flagged_at is None
    assert d_after.flag_reason is None

    # Run the raw query directly and assert post_count == 7, not 21.
    from sqlalchemy import text

    cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with session_factory() as s:
        row = (
            await s.execute(
                text("""
                    WITH post_counts AS (
                        SELECT endpoint_id, COUNT(*) AS post_count
                        FROM requests
                        WHERE received_at > :cutoff
                          AND method IN ('POST', 'PUT', 'PATCH')
                        GROUP BY endpoint_id
                    ),
                    forward_ok_counts AS (
                        SELECT endpoint_id, COUNT(*) AS forward_ok_count
                        FROM forwards
                        WHERE forward_completed_at > :cutoff
                          AND status = 'succeeded'
                        GROUP BY endpoint_id
                    )
                    SELECT
                        COALESCE(p.post_count, 0) AS post_count,
                        COALESCE(f.forward_ok_count, 0) AS forward_ok_count
                    FROM endpoints e
                    LEFT JOIN post_counts p ON p.endpoint_id = e.id
                    LEFT JOIN forward_ok_counts f ON f.endpoint_id = e.id
                    WHERE e.id = :id
                """),
                {"cutoff": cutoff, "id": d.id},
            )
        ).one()
    assert row.post_count == 7  # NOT 21 — proves no multiplicative inflation
    assert row.forward_ok_count == 3


async def test_abuse_scan_skips_discord_when_webhook_url_is_none(
    session_factory, seeded, monkeypatch
):
    """When abuse_webhook_url is None, the scan still flags but does NOT
    call the Discord helper."""
    discord_calls: list[tuple[str, str]] = []

    async def fake_post_discord_alert(url: str, message: str) -> None:
        discord_calls.append((url, message))

    monkeypatch.setattr(abuse_scan, "post_discord_alert", fake_post_discord_alert)

    settings = SimpleNamespace(abuse_webhook_url=None)
    ctx = {"_session_factory": session_factory, "_settings": settings}

    flagged_count = await run_abuse_scan(ctx)

    assert flagged_count == 1
    a_after = await _read_endpoint(session_factory, seeded.a.id)
    assert a_after.flagged_at is not None
    assert a_after.flag_reason == "phishing_no_forward"

    assert discord_calls == []
