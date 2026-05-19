import httpx
from httpx import ASGITransport

from webhook_inspector.web.app.main import app as app_service
from webhook_inspector.web.ingestor.main import app as ingestor_service


async def test_capture_returns_200_and_persists(monkeypatch, database_url, engine, tmp_path):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    # reset caches
    from webhook_inspector.web.app import deps as app_deps
    from webhook_inspector.web.ingestor import deps as ing_deps

    app_deps.get_settings.cache_clear()
    app_deps._engine.cache_clear()
    app_deps._session_factory.cache_clear()
    ing_deps.get_settings.cache_clear()
    ing_deps._engine.cache_clear()
    ing_deps._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.post("/api/endpoints")
        token = resp.json()["token"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        resp = await c.post(f"/h/{token}", json={"hello": "world"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.get(f"/api/endpoints/{token}/requests")
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["method"] == "POST"


async def test_capture_unknown_token_404(monkeypatch, database_url, engine, tmp_path):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    from webhook_inspector.web.ingestor import deps as ing_deps

    ing_deps.get_settings.cache_clear()
    ing_deps._engine.cache_clear()
    ing_deps._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        resp = await c.post("/h/totallymade-up", json={})
        assert resp.status_code == 404


async def test_capture_rejects_oversized_body(monkeypatch, database_url, engine, tmp_path):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MAX_BODY_BYTES", "1024")
    from webhook_inspector.web.app import deps as app_deps
    from webhook_inspector.web.ingestor import deps as ing_deps

    for m in (app_deps, ing_deps):
        m.get_settings.cache_clear()
        m._engine.cache_clear()
        m._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.post("/api/endpoints")
        token = resp.json()["token"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        resp = await c.post(f"/h/{token}", content=b"x" * 2048)
        assert resp.status_code == 413


async def test_capture_post_commit_enqueue_via_background_task(
    monkeypatch, database_url, engine, tmp_path
):
    """When the endpoint has a forward_url, the capture route must enqueue
    the new forward via FastAPI BackgroundTasks (which run AFTER the
    response, which is AFTER the session commit). Verify by snooping on
    the queue and asserting the enqueued forward_id matches what the
    request_repo persisted.
    """
    from tests.fakes.forward_queue import FakeForwardQueue

    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    from webhook_inspector.web.app import deps as app_deps
    from webhook_inspector.web.ingestor import deps as ing_deps

    for m in (app_deps, ing_deps):
        m.get_settings.cache_clear()
        m._engine.cache_clear()
        m._session_factory.cache_clear()

    queue = FakeForwardQueue()
    monkeypatch.setattr(
        "webhook_inspector.web.ingestor.deps.get_forward_queue",
        lambda: queue,
    )

    # Create endpoint with forward_url, capture one request.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.post("/api/endpoints")
        token = resp.json()["token"]
        # Configure the forward URL via PATCH.
        resp = await c.patch(
            f"/api/endpoints/{token}/config",
            json={"forward": {"url": "https://example.com/forward"}},
        )
        assert resp.status_code == 204

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        resp = await c.post(f"/h/{token}", content=b'{"hello":"world"}')
        assert resp.status_code == 200

    # Background task must have run by the time the test client's await
    # returned (FastAPI awaits BackgroundTasks before completing the
    # response cycle through ASGITransport).
    assert len(queue.enqueued) == 1
    enqueued_forward_id, defer = queue.enqueued[0]
    assert defer == 0
    # The enqueued id should match the forward row visible in DB.
    from sqlalchemy import text

    from webhook_inspector.web.ingestor.deps import _session_factory

    factory = _session_factory()
    async with factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, status FROM forwards WHERE endpoint_id = (SELECT id FROM endpoints WHERE token = :t)"
                ),
                {"t": token},
            )
        ).one()
    assert row.id == enqueued_forward_id
    assert row.status == "pending"


async def test_capture_rejects_oversized_chunked_body_without_content_length(
    monkeypatch, database_url, engine, tmp_path
):
    """When the sender uses Transfer-Encoding: chunked (no Content-Length),
    the early header gate is bypassed. The streaming reader must still
    abort once total bytes exceed max_body_bytes — otherwise a chunked
    upload could buffer arbitrary bytes in memory.

    httpx auto-switches to chunked when given a generator instead of a
    bytes buffer (no Content-Length emitted). We assert 413 surfaces
    while the body streams.
    """
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    monkeypatch.setenv("BLOB_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MAX_BODY_BYTES", "1024")
    from webhook_inspector.web.app import deps as app_deps
    from webhook_inspector.web.ingestor import deps as ing_deps

    for m in (app_deps, ing_deps):
        m.get_settings.cache_clear()
        m._engine.cache_clear()
        m._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.post("/api/endpoints")
        token = resp.json()["token"]

    async def _chunked_oversized():
        # 4 chunks of 512 bytes = 2048 bytes total. The first chunk fits;
        # the third pushes total over the 1024-byte cap and must 413.
        for _ in range(4):
            yield b"x" * 512

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        # Passing an async generator → httpx omits Content-Length and uses
        # Transfer-Encoding: chunked.
        resp = await c.post(f"/h/{token}", content=_chunked_oversized())
        assert resp.status_code == 413
        # Defensive: ensure Content-Length wasn't somehow set; that would
        # mean we tested the wrong path (the header pre-check would have
        # caught it before the streaming reader did).
        assert "content-length" not in resp.request.headers


async def test_ingestor_returns_custom_status_body_headers(
    monkeypatch, database_url, engine, tmp_path
):
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
        resp = await c.post(
            "/api/endpoints",
            json={
                "response": {
                    "status_code": 418,
                    "body": '{"teapot":true}',
                    "headers": {"X-Custom": "yes"},
                    "delay_ms": 0,
                }
            },
        )
        token = resp.json()["token"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        resp = await c.post(f"/h/{token}", json={"hello": "world"})
        assert resp.status_code == 418
        assert resp.json() == {"teapot": True}
        assert resp.headers.get("x-custom") == "yes"


async def test_capture_persists_fly_client_ip_header(monkeypatch, database_url, engine, tmp_path):
    """Fly-Client-IP header must propagate to the persisted request's source_ip.

    Under Fly's HTTPS terminator, ``request.client.host`` is the proxy IP,
    not the real client. ``extract_client_ip`` reads the Fly-set header and
    that value must reach the database.
    """
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
        resp = await c.post("/api/endpoints")
        token = resp.json()["token"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        resp = await c.post(
            f"/h/{token}",
            json={"hello": "world"},
            headers={"Fly-Client-IP": "9.9.9.9"},
        )
        assert resp.status_code == 200

    from sqlalchemy import text

    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT source_ip FROM requests "
                    "WHERE endpoint_id = (SELECT id FROM endpoints WHERE token = :token)"
                ),
                {"token": token},
            )
        ).all()
    assert len(rows) == 1
    assert str(rows[0][0]) == "9.9.9.9"


async def test_ingestor_applies_delay(monkeypatch, database_url, engine, tmp_path):
    import time

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
        resp = await c.post("/api/endpoints", json={"response": {"delay_ms": 200}})
        token = resp.json()["token"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=ingestor_service), base_url="http://hook"
    ) as c:
        start = time.monotonic()
        resp = await c.post(f"/h/{token}", content=b"")
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert elapsed >= 0.2  # at least 200ms
