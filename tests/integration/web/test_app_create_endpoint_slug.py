import httpx
from httpx import ASGITransport

from webhook_inspector.web.app.main import app


def _reset_deps():
    from webhook_inspector.web.app import deps

    deps.get_settings.cache_clear()
    deps._engine.cache_clear()
    deps._session_factory.cache_clear()


async def test_create_with_slug_returns_token_equal_to_slug(monkeypatch, database_url, engine):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    _reset_deps()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/endpoints", json={"slug": "my-app-webhooks"})
        assert resp.status_code == 201
        assert resp.json()["token"] == "my-app-webhooks"


async def test_slug_conflict_returns_409(monkeypatch, database_url, engine):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    _reset_deps()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        first = await c.post("/api/endpoints", json={"slug": "duplicate-slug"})
        assert first.status_code == 201
        second = await c.post("/api/endpoints", json={"slug": "duplicate-slug"})
        assert second.status_code == 409
        assert "already" in second.text.lower()


async def test_invalid_slug_returns_400(monkeypatch, database_url, engine):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    _reset_deps()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/endpoints", json={"slug": "FOO"})
        assert resp.status_code == 400


async def test_reserved_slug_returns_400(monkeypatch, database_url, engine):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    _reset_deps()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/endpoints", json={"slug": "api"})
        # The route now returns a hardcoded generic detail (it MUST NOT leak
        # the exception type / reserved set to API consumers — that would be
        # a probing oracle). Asserting status only ; the detail string is
        # exercised by the unit test in tests/unit/web/.
        assert resp.status_code == 400


async def test_create_without_slug_preserves_v1_behavior(monkeypatch, database_url, engine):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    _reset_deps()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/endpoints", json={})
        assert resp.status_code == 201
        token = resp.json()["token"]
        # token_urlsafe(16) → 22 chars base64url
        assert len(token) == 22


async def test_create_endpoint_with_denylisted_slug_returns_400(monkeypatch, database_url, engine):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    _reset_deps()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/endpoints", json={"slug": "stripe-test"})
        # SlugDenylistedError inherits from EndpointValidationError, which the
        # route maps to 400 with detail=str(e). Don't assert the exact message
        # — the route uses str(e), which would couple the test to the
        # exception's formatting. Assert the status code only.
        assert resp.status_code == 400
