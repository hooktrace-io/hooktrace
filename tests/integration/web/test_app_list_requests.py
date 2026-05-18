import httpx
from httpx import ASGITransport

from webhook_inspector.web.app.main import app as app_service
from webhook_inspector.web.ingestor.main import app as ingestor_service


async def test_list_returns_empty_for_new_endpoint(monkeypatch, database_url, engine, tmp_path):
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
        resp = await c.get(f"/api/endpoints/{token}/requests")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "next_before_id": None}


async def test_list_unknown_token_returns_404(monkeypatch, database_url, engine, tmp_path):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    from webhook_inspector.web.app import deps

    deps.get_settings.cache_clear()
    deps._engine.cache_clear()
    deps._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.get("/api/endpoints/missing/requests")
        assert resp.status_code == 404


async def test_list_requests_includes_signature_status(monkeypatch, database_url, engine, tmp_path):
    """GET /api/endpoints/{token}/requests items include signature_status field."""
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    from webhook_inspector.web.app import deps as app_deps
    from webhook_inspector.web.ingestor import deps as ing_deps

    for m in (app_deps, ing_deps):
        m.get_settings.cache_clear()
        m._engine.cache_clear()
        m._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        token = (await c.post("/api/endpoints")).json()["token"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        await c.post(f"/h/{token}", content=b"hello")

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.get(f"/api/endpoints/{token}/requests")
    items = resp.json()["items"]
    assert len(items) == 1
    # CaptureRequest always assigns a non-NULL signature_status: when no
    # provider is configured on the endpoint, it defaults to "no_provider".
    # This invariant matters for PR2's GROUP BY signature_status aggregation.
    assert items[0]["signature_status"] == "no_provider"


async def test_list_requests_fragment_includes_signature_status(
    monkeypatch, database_url, engine, tmp_path
):
    """GET /api/endpoints/{token}/requests.fragment HTML includes signature_status rendering."""
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    from webhook_inspector.web.app import deps as app_deps
    from webhook_inspector.web.ingestor import deps as ing_deps

    for m in (app_deps, ing_deps):
        m.get_settings.cache_clear()
        m._engine.cache_clear()
        m._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        token = (await c.post("/api/endpoints")).json()["token"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        await c.post(f"/h/{token}", content=b"hello")

    # The fragment renders HTML with a "no_provider" badge (slate color) since
    # CaptureRequest defaults signature_status to that string. We only verify
    # the endpoint returns 200 — no UndefinedError, template renders cleanly.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.get(f"/api/endpoints/{token}/requests.fragment")
    assert resp.status_code == 200
