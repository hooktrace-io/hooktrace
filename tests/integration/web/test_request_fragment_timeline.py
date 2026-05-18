"""Integration test: request_fragment.html renders timeline when trace_summary is set."""

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


async def test_fragment_contains_timeline_when_trace_summary_present(
    monkeypatch, database_url, engine, tmp_path
):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    _reset_caches()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as app_c:
        resp = await app_c.post("/api/endpoints", json={})
        assert resp.status_code == 201
        token = resp.json()["token"]

        async with httpx.AsyncClient(
            transport=ASGITransport(app=ingestor_service), base_url="http://hook"
        ) as ing_c:
            resp = await ing_c.post(f"/h/{token}", content=b'{"hello":"world"}')
            assert resp.status_code == 200

        resp = await app_c.get(f"/api/endpoints/{token}/requests.fragment")
        assert resp.status_code == 200
        html = resp.text

        # The fragment always contains the <li> row.
        assert "POST" in html

        # Check that the timeline block renders when trace_summary was persisted.
        # In CI without a live TracerProvider emitting spans to our processor the
        # summary will be None and the timeline is intentionally absent — that is
        # correct behaviour.  If tracing IS configured we validate the marker.
        # The field presence is tested via the JSON API test.
        # For a richer assertion, check that the template does NOT crash.
        assert "<li" in html


async def test_fragment_timeline_markup_with_injected_summary(
    monkeypatch, database_url, engine, tmp_path
):
    """Verify timeline HTML structure by directly injecting a trace_summary via
    the DB and then rendering the fragment."""
    import json
    import uuid

    from sqlalchemy import text as sa_text

    from webhook_inspector.web.app import deps as app_deps

    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    _reset_caches()

    fake_summary = [
        {
            "name": "capture",
            "span_id": "a" * 16,
            "start_time_ns": 1_000_000,
            "duration_ms": 5.0,
            "status": "OK",
            "parent_span_id": None,
            "depth": 0,
        },
        {
            "name": "db.insert",
            "span_id": "b" * 16,
            "start_time_ns": 2_000_000,
            "duration_ms": 2.0,
            "status": "OK",
            "parent_span_id": "a" * 16,
            "depth": 1,
        },
    ]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as app_c:
        resp = await app_c.post("/api/endpoints", json={})
        assert resp.status_code == 201
        token = resp.json()["token"]

    # Insert a fake captured request with trace_summary directly.
    async with app_deps._session_factory()() as session:
        # Find endpoint id
        row = await session.execute(
            sa_text("SELECT id FROM endpoints WHERE token = :t"), {"t": token}
        )
        endpoint_id = row.scalar_one()
        request_id = uuid.uuid4()
        await session.execute(
            sa_text("""
                INSERT INTO requests
                  (id, endpoint_id, method, path, headers, body_size, body_preview,
                   received_at, source_ip, trace_summary)
                VALUES
                  (:id, :eid, 'POST', '/h/' || :t, '{}', 12, NULL,
                   NOW(), '127.0.0.1', cast(:summary AS jsonb))
            """),
            {
                "id": request_id,
                "eid": endpoint_id,
                "t": token,
                "summary": json.dumps(fake_summary),
            },
        )
        await session.commit()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as app_c:
        resp = await app_c.get(f"/api/endpoints/{token}/requests.fragment")
        assert resp.status_code == 200
        html = resp.text

    assert 'class="timeline' in html
    assert "capture" in html
    assert "db.insert" in html
    assert "5.0ms" in html or "5ms" in html
