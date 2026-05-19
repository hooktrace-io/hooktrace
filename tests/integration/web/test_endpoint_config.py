import hashlib
import hmac
import time

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_patch_config_sets_signature_provider(app_client, ingestor_client):
    """End-to-end : PATCH config + signed POST -> captured.signature_status == 'valid'.

    Both fixtures set SECRETS_ENCRYPTION_KEY via conftest so the DI guards
    don't raise RuntimeError.
    """
    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]

    resp = await app_client.patch(
        f"/api/endpoints/{token}/config",
        json={"signature": {"provider": "stripe", "secret": "whsec_test123"}},
    )
    assert resp.status_code == 204

    body = b'{"id":"evt_1"}'
    ts = str(int(time.time()))
    sig = hmac.new(b"whsec_test123", f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"

    resp = await ingestor_client.post(
        f"/h/{token}", content=body, headers={"stripe-signature": header}
    )
    assert resp.status_code == 200

    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    items = resp.json()["items"]
    assert items[0]["signature_status"] == "valid"


@pytest.mark.asyncio
async def test_patch_config_rejects_unknown_provider(app_client):
    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]
    resp = await app_client.patch(
        f"/api/endpoints/{token}/config",
        json={"signature": {"provider": "not-a-real-thing", "secret": "x"}},
    )
    # 422 from Pydantic Literal validation at the boundary.
    assert resp.status_code == 422
    assert "provider" in resp.text.lower()


@pytest.mark.asyncio
async def test_patch_config_unknown_endpoint_returns_404(app_client):
    resp = await app_client.patch(
        "/api/endpoints/nonexistent-token/config",
        json={"signature": {"provider": "stripe", "secret": "whsec_x"}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_config_sets_forward(app_client, ingestor_client, session):
    """PATCH /config with forward block + capture → forwards row created (pending).

    No worker is running in tests so the row stays pending.
    Also verifies the forward_secret is stored encrypted (not in plaintext).
    """
    # Create endpoint
    resp = await app_client.post("/api/endpoints", json={})
    assert resp.status_code == 201
    token = resp.json()["token"]

    # Set forward config with a secret
    fwd_secret = "whfwd_secret_xyz"
    resp = await app_client.patch(
        f"/api/endpoints/{token}/config",
        json={"forward": {"url": "https://example.com/wh", "secret": fwd_secret}},
    )
    assert resp.status_code == 204

    # Verify forward_secret_encrypted is set and not plaintext
    row = await session.execute(
        text("SELECT forward_url, forward_secret_encrypted FROM endpoints WHERE token = :t"),
        {"t": token},
    )
    ep_row = row.one()
    assert ep_row.forward_url == "https://example.com/wh"
    assert ep_row.forward_secret_encrypted is not None
    assert ep_row.forward_secret_encrypted != fwd_secret.encode()

    # Capture a request via ingestor
    resp = await ingestor_client.post(f"/h/{token}", content=b'{"event":"test"}')
    assert resp.status_code == 200

    # Confirm capture worked
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    # The forwards table must have a pending row for this endpoint
    fwd_row_result = await session.execute(
        text(
            "SELECT f.status, f.target_url FROM forwards f"
            " JOIN endpoints e ON e.id = f.endpoint_id"
            " WHERE e.token = :t"
        ),
        {"t": token},
    )
    fwd_rows = fwd_row_result.all()
    assert len(fwd_rows) == 1
    assert fwd_rows[0].status == "pending"
    assert fwd_rows[0].target_url == "https://example.com/wh"
