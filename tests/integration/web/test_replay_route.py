"""Integration tests for POST /api/endpoints/{token}/requests/{id}/replay.

`respx` mocks at the httpx layer. The SSRF guard's DNS resolve is the only
piece outside respx ; monkeypatch `_resolve` to return a public IP so the
guard passes.

Uses `app_client` + `ingestor_client` fixtures from conftest.
"""

import pytest
import respx
from httpx import Response


@pytest.mark.asyncio
async def test_replay_succeeds(app_client, ingestor_client, monkeypatch):
    # 1. Create endpoint
    resp = await app_client.post("/api/endpoints", json={})
    assert resp.status_code == 201
    token = resp.json()["token"]

    # 2. Capture
    resp = await ingestor_client.post(f"/h/{token}", content=b'{"hello":"world"}')
    assert resp.status_code == 200

    # 3. Get request_id
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    request_id = resp.json()["items"][0]["id"]

    # 4. Stub DNS to a public IP and mock the target
    monkeypatch.setattr(
        "webhook_inspector.infrastructure.http.safe_replay_target._resolve",
        lambda host: ["93.184.216.34"],
    )

    with respx.mock:
        respx.post("https://example.com/webhook").mock(
            return_value=Response(200, json={"ok": True}, headers={"x-target": "ok"}),
        )
        resp = await app_client.post(
            f"/api/endpoints/{token}/requests/{request_id}/replay",
            json={"target_url": "https://example.com/webhook"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status_code"] == 200
    assert body["error"] is None
    assert body["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_replay_rejects_private_target_url(app_client, ingestor_client):
    """SSRF-blocked target → 200 response with SsrfBlockedError in error field
    (the block is a "soft" outcome, not an HTTP error from the API).
    """
    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]
    await ingestor_client.post(f"/h/{token}", content=b"x")
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    request_id = resp.json()["items"][0]["id"]

    resp = await app_client.post(
        f"/api/endpoints/{token}/requests/{request_id}/replay",
        json={"target_url": "http://10.0.0.1/x"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status_code"] is None
    assert "SsrfBlockedError" in body["error"]


@pytest.mark.asyncio
async def test_replay_rejects_self_pointing_url(app_client, ingestor_client):
    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]
    await ingestor_client.post(f"/h/{token}", content=b"x")
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    request_id = resp.json()["items"][0]["id"]

    resp = await app_client.post(
        f"/api/endpoints/{token}/requests/{request_id}/replay",
        json={"target_url": "https://app.hooktrace.io/leak"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status_code"] is None
    assert "host suffix blocked" in body["error"]


@pytest.mark.asyncio
async def test_replay_404_for_unknown_endpoint(app_client):
    from uuid import uuid4

    resp = await app_client.post(
        f"/api/endpoints/unknown_token/requests/{uuid4()}/replay",
        json={"target_url": "https://example.com/x"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_404_for_cross_endpoint_request_id(app_client, ingestor_client, monkeypatch):
    """Endpoint A's token + Endpoint B's request_id → 404 (not 403).
    Don't leak existence of the request_id under a different token.
    """
    # Create two endpoints
    r1 = await app_client.post("/api/endpoints", json={})
    r2 = await app_client.post("/api/endpoints", json={})
    token_a = r1.json()["token"]
    token_b = r2.json()["token"]

    # Capture under token_b, get its request_id
    await ingestor_client.post(f"/h/{token_b}", content=b"hello-b")
    resp = await app_client.get(f"/api/endpoints/{token_b}/requests")
    request_id_b = resp.json()["items"][0]["id"]

    # Try to replay token_b's request under token_a — must 404
    resp = await app_client.post(
        f"/api/endpoints/{token_a}/requests/{request_id_b}/replay",
        json={"target_url": "https://example.com/x"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_records_network_error_as_failure(app_client, ingestor_client, monkeypatch):
    """Target raises httpx.ConnectError → Replay row has error set."""
    import httpx as _httpx

    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]
    await ingestor_client.post(f"/h/{token}", content=b"x")
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    request_id = resp.json()["items"][0]["id"]

    monkeypatch.setattr(
        "webhook_inspector.infrastructure.http.safe_replay_target._resolve",
        lambda host: ["93.184.216.34"],
    )
    with respx.mock:
        respx.post("https://example.com/webhook").mock(
            side_effect=_httpx.ConnectError("Connection refused"),
        )
        resp = await app_client.post(
            f"/api/endpoints/{token}/requests/{request_id}/replay",
            json={"target_url": "https://example.com/webhook"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status_code"] is None
    assert "ConnectError" in body["error"]
