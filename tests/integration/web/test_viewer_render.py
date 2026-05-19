import httpx
from httpx import ASGITransport

from webhook_inspector.web.app.main import app as app_service


async def test_viewer_renders_with_token(monkeypatch, database_url, engine, tmp_path):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    from webhook_inspector.web.app import deps

    deps.get_settings.cache_clear()
    deps._engine.cache_clear()
    deps._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        token = (await c.post("/api/endpoints")).json()["token"]
        resp = await c.get(f"/{token}")
        assert resp.status_code == 200
        body = resp.text
        assert token in body
        assert "htmx" in body.lower()
        assert "sse-connect" in body


async def test_viewer_404_for_unknown_token(monkeypatch, database_url, engine, tmp_path):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    from webhook_inspector.web.app import deps

    deps.get_settings.cache_clear()
    deps._engine.cache_clear()
    deps._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.get("/totally-unknown-token-here")
        assert resp.status_code == 404


async def test_viewer_shows_expiry_countdown(app_client):
    """V3 launch-prep PR11: countdown badge in viewer header.

    A brand-new endpoint = 30-day TTL, so the badge reads "Expires in 30 days".
    Color coding (slate ≥7d, amber-400 <7d, rose-400 <1d) is asserted by the
    presence of `text-slate-500` for a fresh endpoint.
    """
    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]
    viewer = await app_client.get(f"/{token}")
    assert viewer.status_code == 200
    # New endpoint with default 30-day TTL.
    assert "Expires in" in viewer.text
    assert "30 day" in viewer.text  # tolerates "30 days"
    # >=7d → slate color, NOT amber/rose.
    assert "text-slate-500" in viewer.text
