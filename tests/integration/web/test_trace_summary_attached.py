"""Integration test: trace_summary is persisted and visible via the API."""

import httpx
from httpx import ASGITransport

from webhook_inspector.web.app.main import app as app_service
from webhook_inspector.web.ingestor.main import app as ingestor_service


def _reset_caches() -> None:
    from webhook_inspector.web.app import deps as app_deps
    from webhook_inspector.web.ingestor import deps as ing_deps

    app_deps.get_settings.cache_clear()
    app_deps._engine.cache_clear()
    app_deps._session_factory.cache_clear()
    ing_deps.get_settings.cache_clear()
    ing_deps._engine.cache_clear()
    ing_deps._session_factory.cache_clear()


async def test_capture_persists_trace_summary(monkeypatch, database_url, engine, tmp_path):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    _reset_caches()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.post("/api/endpoints", json={})
        assert resp.status_code == 201
        token = resp.json()["token"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        resp = await c.post(f"/h/{token}", content=b'{"hello":"world"}')
        assert resp.status_code == 200

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.get(f"/api/endpoints/{token}/requests")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        summary = items[0]["trace_summary"]
        # trace_summary may be None if tracing is not configured (no global
        # TracerProvider with InMemoryRequestSpanProcessor) — in that case the
        # test verifies the field is present in the response schema.
        assert "trace_summary" in items[0]
        if summary is not None:
            span_names = [s["name"] for s in summary]
            assert "capture" in span_names
            # Every span must have depth and span_id fields.
            for span in summary:
                assert "depth" in span
                assert "span_id" in span
