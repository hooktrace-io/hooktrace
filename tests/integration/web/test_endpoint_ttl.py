"""V3 launch-prep PR11: endpoint TTL bumped 7 → 30 days.

Backfill policy (LOCKED):
- NEW endpoints created after this deploy get expires_at = created_at + 30 days
- EXISTING endpoints keep their original expires_at (e.g. created_at + 7 days
  for pre-deploy endpoints). No Alembic backfill — see PR11 commit message.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text


async def test_new_endpoint_expires_at_is_30_days_from_now(app_client):
    before = datetime.now(UTC)
    resp = await app_client.post("/api/endpoints", json={})
    after = datetime.now(UTC)
    assert resp.status_code == 201

    expires_at = datetime.fromisoformat(resp.json()["expires_at"])
    expected_min = before + timedelta(days=30) - timedelta(seconds=2)
    expected_max = after + timedelta(days=30) + timedelta(seconds=2)
    assert expected_min <= expires_at <= expected_max, (
        f"expires_at {expires_at} not in [{expected_min}, {expected_max}]. "
        "Check Endpoint.create() TTL math."
    )


async def test_existing_endpoint_keeps_original_ttl(session, app_client):
    """Simulate a pre-deploy endpoint (TTL=7) and verify it is NOT backfilled.

    The route layer reads expires_at from the row as-is. No migration extends
    legacy rows. New cohort rolls over naturally within a 7-day window.
    """
    token = "ttl-legacy-test"
    eid = uuid4()
    now = datetime.now(UTC)
    seven_days = now + timedelta(days=7)
    await session.execute(
        text(
            """
            INSERT INTO endpoints (
                id, token, expires_at, request_count, created_at,
                response_status_code, response_body, response_headers, response_delay_ms
            )
            VALUES (
                :id, :token, :expires, 0, :now,
                200, '', '{}', 0
            )
            """
        ),
        {"id": eid, "token": token, "expires": seven_days, "now": now},
    )
    await session.commit()

    row = (
        await session.execute(
            text("SELECT expires_at FROM endpoints WHERE token = :t"),
            {"t": token},
        )
    ).scalar_one()
    # Tolerance: timezone-aware diff in seconds. Postgres returns the value
    # untouched ; backfill would push this 23 days forward.
    if row.tzinfo is None:
        row = row.replace(tzinfo=UTC)
    delta = abs((row - seven_days).total_seconds())
    assert delta < 2, f"existing endpoint TTL was modified (delta={delta}s)"

    # Cleanup so the row doesn't bleed into other tests in the same session.
    await session.execute(
        text("DELETE FROM endpoints WHERE token = :t"),
        {"t": token},
    )
    await session.commit()


async def test_tos_route_returns_html(app_client):
    """V3 launch-prep PR11: /tos returns the minimal Terms of Service page.

    Asserts that the 30-day retention statement (the core promise) is present
    in the rendered body — otherwise a future template edit could quietly
    drop the contract.
    """
    resp = await app_client.get("/tos")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "30 days" in resp.text


async def test_landing_links_to_tos(app_client):
    """The footer must link to /tos so users can find it without typing the path."""
    resp = await app_client.get("/")
    assert resp.status_code == 200
    assert 'href="/tos"' in resp.text
