"""Integration tests for GET /api/endpoints/{token}/integrations.

Requires Docker (testcontainers Postgres) — CI boots Postgres automatically.
Uses the app_client + ingestor_client fixtures from conftest.py.
"""

import pytest

from tests._helpers.stripe import stripe_signature


@pytest.mark.asyncio
async def test_integrations_endpoint_returns_aggregated_with_signature_status(
    app_client, ingestor_client
):
    """End-to-end: mixed Stripe + GitHub requests → aggregated by integration
    with correct signature_status_counts.
    """
    # Create endpoint and configure Stripe signature verification
    resp = await app_client.post("/api/endpoints", json={})
    assert resp.status_code == 201
    token = resp.json()["token"]

    secret = "whsec_test"
    resp = await app_client.patch(
        f"/api/endpoints/{token}/config",
        json={"signature": {"provider": "stripe", "secret": secret}},
    )
    assert resp.status_code == 204

    # 1. Valid Stripe request (charge.succeeded)
    valid_payload = b'{"id":"evt","type":"charge.succeeded","data":{}}'
    resp = await ingestor_client.post(
        f"/h/{token}",
        content=valid_payload,
        headers={"stripe-signature": stripe_signature(secret=secret, body=valid_payload)},
    )
    assert resp.status_code == 200

    # 2. Invalid Stripe request (forged / bogus signature)
    forged_payload = b'{"forged":true}'
    resp = await ingestor_client.post(
        f"/h/{token}",
        content=forged_payload,
        headers={"stripe-signature": "v1=bogus"},
    )
    assert resp.status_code == 200

    # 3. GitHub request (no Stripe sig, different integration)
    github_payload = b'{"action":"opened"}'
    resp = await ingestor_client.post(
        f"/h/{token}",
        content=github_payload,
        headers={
            "x-github-event": "pull_request",
            "x-github-delivery": "uuid-abc-123",
        },
    )
    assert resp.status_code == 200

    # Fetch aggregations
    resp = await app_client.get(f"/api/endpoints/{token}/integrations")
    assert resp.status_code == 200

    data = resp.json()
    by_int = {item["integration"]: item for item in data}

    assert "stripe" in by_int, f"stripe not in response: {data}"
    assert by_int["stripe"]["total"] == 2
    # Valid + invalid — exact counts depend on ingestor detection
    stripe_statuses = by_int["stripe"]["signature_status_counts"]
    assert stripe_statuses.get("valid", 0) == 1, f"expected 1 valid, got: {stripe_statuses}"
    assert stripe_statuses.get("invalid", 0) == 1, f"expected 1 invalid, got: {stripe_statuses}"

    assert "github" in by_int, f"github not in response: {data}"
    assert by_int["github"]["total"] == 1


@pytest.mark.asyncio
async def test_integrations_endpoint_returns_404_for_unknown_token(app_client):
    """GET /api/endpoints/{token}/integrations returns 404 for nonexistent token."""
    resp = await app_client.get("/api/endpoints/nonexistent-token-xyz/integrations")
    assert resp.status_code == 404
    assert "endpoint not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_integrations_endpoint_returns_empty_for_no_requests(app_client):
    """Endpoint with zero captured requests returns empty list."""
    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]

    resp = await app_client.get(f"/api/endpoints/{token}/integrations")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_integrations_html_view_renders(app_client, ingestor_client):
    """GET /{token}/integrations renders the integrations.html template."""
    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]

    resp = await app_client.get(f"/{token}/integrations")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "hooktrace" in resp.text
    # Empty state — no integrations yet
    assert token in resp.text
