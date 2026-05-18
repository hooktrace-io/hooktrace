"""Unit tests for integrations.html template rendering.

No database required — exercises Jinja2 template directly.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from webhook_inspector.domain.entities.integration_aggregate import IntegrationAggregate

_TEMPLATES_DIR = (
    Path(__file__).parents[3] / "src" / "webhook_inspector" / "web" / "app" / "templates"
)


def _env() -> Environment:
    from webhook_inspector.web.app.template_globals import apply_globals

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    apply_globals(env)
    return env


def _render_integrations(
    token: str = "tok123",
    aggregates: list[IntegrationAggregate] | None = None,
) -> str:
    env = _env()
    template = env.get_template("integrations.html")
    return template.render(
        token=token,
        aggregates=aggregates or [],
    )


def test_integrations_template_renders_without_error():
    """Template renders without UndefinedError for empty aggregates."""
    html = _render_integrations()
    assert "<html" in html or "<!DOCTYPE" in html.lower() or "hooktrace" in html


def test_integrations_template_shows_integration_name():
    """Integration name appears in rendered HTML."""
    aggregates = [
        IntegrationAggregate(
            integration="stripe",
            total=5,
            event_types={"charge.succeeded": 3, "charge.refunded": 2},
            signature_status_counts={"valid": 4, "invalid": 1},
        )
    ]
    html = _render_integrations(aggregates=aggregates)
    assert "stripe" in html
    assert "5" in html


def test_integrations_template_shows_signature_status_counts():
    """Signature status breakdown appears in rendered HTML."""
    aggregates = [
        IntegrationAggregate(
            integration="github",
            total=3,
            event_types={"push": 2, "pull_request": 1},
            signature_status_counts={"valid": 2, "missing": 1},
        )
    ]
    html = _render_integrations(aggregates=aggregates)
    assert "valid" in html
    assert "missing" in html
    assert "github" in html


def test_integrations_template_shows_event_types():
    """Event type sub-breakdown appears in rendered HTML."""
    aggregates = [
        IntegrationAggregate(
            integration="stripe",
            total=2,
            event_types={"charge.succeeded": 2},
            signature_status_counts={"valid": 2},
        )
    ]
    html = _render_integrations(aggregates=aggregates)
    assert "charge.succeeded" in html


def test_integrations_template_shows_empty_state_gracefully():
    """When aggregates is empty, no crash and some content is shown."""
    html = _render_integrations(aggregates=[])
    assert "<html" in html or "hooktrace" in html or "integrations" in html.lower()


def test_viewer_html_has_integrations_link():
    """viewer.html must contain a link to the integrations page."""
    env = _env()
    template = env.get_template("viewer.html")
    # Render with minimal context matching the viewer route
    html = template.render(
        token="tok123",
        hook_url="http://hook.test/h/tok123",
        initial_requests=[],
    )
    assert "/integrations" in html or "integrations" in html.lower()
