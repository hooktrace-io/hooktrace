import hashlib
import hmac
import time

import pytest


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
