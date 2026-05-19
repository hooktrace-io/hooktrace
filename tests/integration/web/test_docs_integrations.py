"""Integration tests for the public /docs/integrations route (PR13).

Covers:
- Index page lists all 9 services + the verifying-forwards guide.
- Each of the 10 doc pages renders 200 + HTML content-type.
- Unknown slug → 404 (paypal is deferred to V3.5 per CLAUDE.md, so it must
  NOT be served).
- Path-traversal attempts via the slug parameter are rejected by the
  allowlist before any filesystem access.
- The verifying-forwards page contains the load-bearing signature contract
  strings (header name, algorithm, t=/v1= shape).
- The landing footer's `/docs/integrations` link is reachable (regression
  guard against the footer breaking after this PR).
"""

import httpx
import pytest


@pytest.mark.asyncio
async def test_docs_index_lists_all_integrations(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.get("/docs/integrations")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body_lower = resp.text.lower()
    for service in (
        "stripe",
        "github",
        "shopify",
        "twilio",
        "mailgun",
        "discord",
        "slack",
        "zapier",
        "n8n",
    ):
        assert service in body_lower, f"index page must mention {service}"
    assert "verifying-forwards" in resp.text or "verifying forwards" in body_lower


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slug",
    [
        "stripe",
        "github",
        "shopify",
        "twilio",
        "mailgun",
        "discord",
        "slack",
        "zapier",
        "n8n",
        "verifying-forwards",
    ],
)
async def test_doc_page_renders(app_client: httpx.AsyncClient, slug: str) -> None:
    resp = await app_client.get(f"/docs/integrations/{slug}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # Sanity check that the markdown renderer ran: every page has at least
    # one <h1> tag, since each .md starts with a level-1 heading.
    assert "<h1>" in resp.text


@pytest.mark.asyncio
async def test_doc_page_unknown_slug_returns_404(app_client: httpx.AsyncClient) -> None:
    # PayPal validator is deferred to V3.5 per CLAUDE.md — the route must
    # not serve a page for it until the doc + validator are added.
    resp = await app_client.get("/docs/integrations/paypal")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_doc_page_unknown_slug_arbitrary_returns_404(
    app_client: httpx.AsyncClient,
) -> None:
    resp = await app_client.get("/docs/integrations/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_doc_page_rejects_path_traversal(app_client: httpx.AsyncClient) -> None:
    # URL-encoded ../config — slug allowlist rejects anything not in the
    # closed set, so this never reaches the filesystem.
    resp = await app_client.get("/docs/integrations/..%2Fconfig")
    assert resp.status_code == 404

    # Literal traversal — FastAPI's path normalization typically rewrites
    # this before routing, so we accept either 404 (allowlist miss) or 405
    # (no matching route for the normalized path).
    resp = await app_client.get("/docs/integrations/../../etc/passwd")
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_verifying_forwards_doc_mentions_signature_header(
    app_client: httpx.AsyncClient,
) -> None:
    resp = await app_client.get("/docs/integrations/verifying-forwards")
    assert resp.status_code == 200
    assert "X-Hooktrace-Signature" in resp.text
    assert "HMAC-SHA256" in resp.text
    assert "t=" in resp.text
    assert "v1=" in resp.text
    # The retry policy table must match the code constants in
    # domain/entities/forward.py:RETRY_BACKOFFS = (30, 120, 600, 3600, 14400).
    # Delays expressed in human form: 30 seconds, 2 minutes, 10 minutes,
    # 1 hour. The 14400s (4h) entry is unused (see forward_decision.decide)
    # and intentionally omitted from the public table.
    body_lower = resp.text.lower()
    assert "30 seconds" in body_lower
    assert "2 minutes" in body_lower
    assert "10 minutes" in body_lower
    assert "1 hour" in body_lower


@pytest.mark.asyncio
async def test_landing_footer_link_to_docs_integrations_resolves(
    app_client: httpx.AsyncClient,
) -> None:
    """Regression guard: the landing footer (PR12) links to /docs/integrations
    and must not 404 once PR13 lands."""
    landing = await app_client.get("/")
    assert landing.status_code == 200
    assert "/docs/integrations" in landing.text

    docs = await app_client.get("/docs/integrations")
    assert docs.status_code == 200


@pytest.mark.asyncio
async def test_stripe_doc_cites_exact_header(app_client: httpx.AsyncClient) -> None:
    """Spot-check that the rendered docs match the validator implementations.

    A diverging doc would silently mislead users; this catches the most-cited
    one (Stripe) end-to-end."""
    resp = await app_client.get("/docs/integrations/stripe")
    assert resp.status_code == 200
    assert "Stripe-Signature" in resp.text
    assert "HMAC-SHA256" in resp.text


@pytest.mark.asyncio
async def test_github_doc_cites_exact_header(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.get("/docs/integrations/github")
    assert resp.status_code == 200
    assert "X-Hub-Signature-256" in resp.text


@pytest.mark.asyncio
async def test_discord_doc_calls_out_ed25519(app_client: httpx.AsyncClient) -> None:
    """Discord uses Ed25519 (asymmetric), not HMAC — the doc must say so to
    avoid users pasting an HMAC secret instead of the application public key.
    """
    resp = await app_client.get("/docs/integrations/discord")
    assert resp.status_code == 200
    assert "Ed25519" in resp.text
    assert "public key" in resp.text.lower()
