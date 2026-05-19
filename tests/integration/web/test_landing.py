import httpx
from httpx import ASGITransport

from webhook_inspector.web.app.main import app as app_service


async def test_landing_page_renders_at_root(monkeypatch, database_url, engine):
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    from webhook_inspector.web.app import deps

    deps.get_settings.cache_clear()
    deps._engine.cache_clear()
    deps._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.get("/")
        assert resp.status_code == 200
        body = resp.text
        assert "hooktrace" in body
        assert "Create a webhook URL" in body
        assert "/api/endpoints" in body  # the htmx hx-post target
        assert resp.headers["content-type"].startswith("text/html")


async def test_landing_page_has_og_tags(monkeypatch, database_url, engine):
    """OG tags enable proper social-sharing previews."""
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+psycopg_async", "+psycopg"))
    from webhook_inspector.web.app import deps

    deps.get_settings.cache_clear()
    deps._engine.cache_clear()
    deps._session_factory.cache_clear()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_service), base_url="http://test"
    ) as c:
        resp = await c.get("/")
        assert 'property="og:title"' in resp.text
        assert 'property="og:description"' in resp.text


async def test_landing_includes_positioning_headline(app_client):
    """V3 launch-prep PR12: the locked pitch is 'observability layer for webhooks'.

    Asserts the strategic-pivot positioning keyword is rendered so a future
    template refactor doesn't quietly revert to the 'capture/inspect' framing.
    """
    resp = await app_client.get("/")
    assert resp.status_code == 200
    assert "observability" in resp.text.lower()


async def test_landing_lists_supported_integrations(app_client):
    """The features section advertises the 9 supported integrations.

    Stripe + GitHub are required as proxies for the feature-card section
    actually being rendered (they're the two most-visible integrations).
    """
    resp = await app_client.get("/")
    assert resp.status_code == 200
    assert "Stripe" in resp.text
    assert "GitHub" in resp.text


async def test_landing_has_github_link(app_client):
    """The GitHub badge links to the public repo so visitors can star it."""
    resp = await app_client.get("/")
    assert resp.status_code == 200
    assert "github.com/hooktrace-io/hooktrace" in resp.text


async def test_landing_has_docs_integrations_link(app_client):
    """The footer reserves a slot for /docs/integrations (populated in Block 3).

    The route 404s today — that's expected; we're testing the link presence so
    that when Block 3 adds the route, the footer already points at it.
    """
    resp = await app_client.get("/")
    assert resp.status_code == 200
    assert "/docs/integrations" in resp.text


async def test_landing_meta_description_mentions_features(app_client):
    """The <meta name="description"> should reference at least one V3 feature
    (HMAC / replay / forward) so the snippet shown by search engines and
    link-previews matches the new positioning."""
    resp = await app_client.get("/")
    assert resp.status_code == 200
    # Extract the meta description tag content.
    import re

    m = re.search(
        r'<meta\s+name="description"\s+content="([^"]+)"',
        resp.text,
    )
    assert m is not None, "meta description tag missing"
    desc = m.group(1).lower()
    assert any(kw in desc for kw in ("hmac", "replay", "forward")), (
        f"meta description should mention HMAC/replay/forward, got: {desc!r}"
    )
