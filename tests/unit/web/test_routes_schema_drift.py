"""Unit tests for schema_drift propagation through the read surfaces.

These tests verify each surface without requiring a running Postgres (no
testcontainers) by exercising schemas, dicts, and template rendering directly.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, select_autoescape

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.web.app.routes import RequestItem
from webhook_inspector.web.app.template_globals import apply_globals

_TEMPLATES_DIR = (
    Path(__file__).parents[3] / "src" / "webhook_inspector" / "web" / "app" / "templates"
)


def _make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(),
    )
    apply_globals(env)
    return env


def _make_request(schema_drift: dict | None = None) -> CapturedRequest:
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
        signature_status="no_provider",
        schema_drift=schema_drift,
    )


# ---------------------------------------------------------------------------
# Surface 2: RequestItem Pydantic schema includes schema_drift
# ---------------------------------------------------------------------------


def test_request_item_includes_schema_drift_field():
    """RequestItem schema must serialise schema_drift when populated."""
    drift = {"added": ["data"], "removed": [], "changed": []}
    r = _make_request(schema_drift=drift)
    item = RequestItem(
        id=r.id,
        method=r.method,
        path=r.path,
        headers=r.headers,
        body_preview=r.body_preview,
        body_size=r.body_size,
        received_at=r.received_at.isoformat(),
        signature_status=r.signature_status,
        schema_drift=r.schema_drift,
    )
    payload = item.model_dump()
    assert "schema_drift" in payload
    assert payload["schema_drift"] == drift


def test_request_item_schema_drift_none_by_default():
    """schema_drift defaults to None when not provided."""
    r = _make_request(schema_drift=None)
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
    assert payload["schema_drift"] is None


# ---------------------------------------------------------------------------
# Surface 4/6: Jinja template badge rendering
# ---------------------------------------------------------------------------


def _req_dict(schema_drift: dict | None = None) -> dict:
    r = _make_request(schema_drift=schema_drift)
    return {
        "method": r.method,
        "path": r.path,
        "body_size": r.body_size,
        "received_at": r.received_at.isoformat(),
        "headers": r.headers,
        "body_preview": r.body_preview,
        "signature_status": r.signature_status,
        "detected_integration": r.detected_integration,
        "detected_event_type": r.detected_event_type,
        "schema_drift": r.schema_drift,
    }


def _render_fragment(schema_drift: dict | None) -> str:
    env = _make_env()
    template = env.get_template("request_fragment.html")
    return template.render(
        req=_req_dict(schema_drift=schema_drift),
        hook_url="http://hook.test",
    )


def test_fragment_renders_drift_badge_when_added():
    """Badge appears when schema_drift.added is non-empty."""
    html = _render_fragment({"added": ["data.object"], "removed": [], "changed": []})
    assert "drift" in html
    assert "amber" in html


def test_fragment_renders_drift_badge_when_changed():
    """Badge appears when schema_drift.changed is non-empty."""
    html = _render_fragment({"added": [], "removed": [], "changed": ["data.amount"]})
    assert "drift" in html
    assert "amber" in html


def test_fragment_no_drift_badge_when_only_removed():
    """Badge must NOT appear when only removed fields (not added or changed)."""
    html = _render_fragment({"added": [], "removed": ["old_field"], "changed": []})
    assert "drift" not in html


def test_fragment_no_drift_badge_when_none():
    """Badge must NOT appear when schema_drift is None."""
    html = _render_fragment(None)
    assert "drift" not in html
    assert "<li" in html


def test_fragment_no_drift_badge_when_empty_drift():
    """Badge must NOT appear when all lists are empty."""
    html = _render_fragment({"added": [], "removed": [], "changed": []})
    assert "drift" not in html
    assert "<li" in html
