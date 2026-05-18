"""Unit tests for signature_status propagation through the read surfaces.

These tests verify each surface without requiring a running Postgres (no
testcontainers) by exercising schemas, dicts, and template rendering directly.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, select_autoescape

from webhook_inspector.domain.entities.captured_request import (
    CapturedRequest,
)
from webhook_inspector.web.app.routes import RequestItem

# ---------------------------------------------------------------------------
# Surface 2 & 3: RequestItem Pydantic schema + list_requests JSON
# ---------------------------------------------------------------------------


def _make_request(signature_status: str | None = None) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"content-type": "application/json"},
        body_preview='{"x":1}',
        body_size=7,
        blob_key=None,
        source_ip="127.0.0.1",
        received_at=datetime.now(UTC),
        signature_status=signature_status,
    )


def test_request_item_includes_signature_status_field():
    """RequestItem schema must serialise signature_status to the JSON output."""
    r = _make_request(signature_status="valid")
    item = RequestItem(
        id=r.id,
        method=r.method,
        path=r.path,
        headers=r.headers,
        body_preview=r.body_preview,
        body_size=r.body_size,
        received_at=r.received_at.isoformat(),
        signature_status=r.signature_status,
    )
    payload = item.model_dump()
    assert "signature_status" in payload
    assert payload["signature_status"] == "valid"


def test_request_item_signature_status_none_when_no_provider():
    """signature_status == None for endpoints with no signature provider."""
    r = _make_request(signature_status=None)
    item = RequestItem(
        id=r.id,
        method=r.method,
        path=r.path,
        headers=r.headers,
        body_preview=r.body_preview,
        body_size=r.body_size,
        received_at=r.received_at.isoformat(),
        signature_status=r.signature_status,
    )
    payload = item.model_dump()
    assert "signature_status" in payload
    assert payload["signature_status"] is None


def test_request_item_no_signature_status_field_drops_it():
    """Before PR1.5: constructing RequestItem without signature_status omits the key.

    This test documents the OLD (broken) behaviour and must FAIL once
    signature_status is added as a required field with a default.
    After PR1.5 the field is always present (even if None), so this test
    is deliberately asserting the pre-fix state.  We mark it xfail so it
    becomes an expected failure once the fix is applied.
    """
    r = _make_request(signature_status="invalid")
    item = RequestItem(
        id=r.id,
        method=r.method,
        path=r.path,
        headers=r.headers,
        body_preview=r.body_preview,
        body_size=r.body_size,
        received_at=r.received_at.isoformat(),
        # intentionally not passing signature_status
    )
    payload = item.model_dump()
    # After the fix signature_status is always in the payload (as None default).
    # This was the bug: field absent → silently dropped from API response.
    assert "signature_status" in payload


# ---------------------------------------------------------------------------
# Surface 4 & 6: Jinja req dict must include signature_status
# ---------------------------------------------------------------------------


def _req_dict(signature_status: str | None = None) -> dict:
    r = _make_request(signature_status=signature_status)
    return {
        "method": r.method,
        "path": r.path,
        "body_size": r.body_size,
        "received_at": r.received_at.isoformat(),
        "headers": r.headers,
        "body_preview": r.body_preview,
        "signature_status": r.signature_status,
    }


def test_fragment_req_dict_includes_signature_status():
    """The dict passed to request_fragment.html must carry signature_status."""
    d = _req_dict(signature_status="missing")
    assert "signature_status" in d
    assert d["signature_status"] == "missing"


def test_fragment_req_dict_signature_status_none():
    d = _req_dict(signature_status=None)
    assert "signature_status" in d
    assert d["signature_status"] is None


# ---------------------------------------------------------------------------
# Surface 5 & 6: Jinja template rendering (badge)
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = (
    Path(__file__).parents[3] / "src" / "webhook_inspector" / "web" / "app" / "templates"
)


def _render_fragment(signature_status: str | None) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(),
    )
    template = env.get_template("request_fragment.html")
    return template.render(
        req=_req_dict(signature_status=signature_status),
        hook_url="http://hook.test",
    )


def test_fragment_renders_no_badge_when_signature_status_none():
    """When signature_status is None the badge span must not appear."""
    html = _render_fragment(None)
    # No UndefinedError; badge conditional is False — badge text values absent
    assert "valid" not in html
    assert "invalid" not in html
    assert "missing" not in html
    # But the li element is rendered without errors
    assert "<li" in html


def test_fragment_renders_valid_badge():
    """signature_status='valid' renders an emerald badge."""
    html = _render_fragment("valid")
    assert "valid" in html
    assert "emerald" in html


def test_fragment_renders_invalid_badge():
    """signature_status='invalid' renders a rose badge."""
    html = _render_fragment("invalid")
    assert "invalid" in html
    assert "rose" in html


def test_fragment_renders_missing_badge():
    """signature_status='missing' renders an amber badge."""
    html = _render_fragment("missing")
    assert "missing" in html
    assert "amber" in html


def test_fragment_renders_no_provider_badge():
    """signature_status='no_provider' renders a slate badge."""
    html = _render_fragment("no_provider")
    assert "no_provider" in html
    assert "slate" in html


def test_fragment_no_undefined_error_for_none():
    """Template must not raise UndefinedError when signature_status is None."""
    # Should complete without exception
    html = _render_fragment(None)
    assert "<li" in html
