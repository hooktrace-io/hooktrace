"""Color palettes shared across Jinja templates.

Both the request_fragment list view and the integrations aggregation view
read these maps. Call `apply_globals(env)` after constructing any Jinja
Environment that renders these templates ; that keeps the single source of
truth here while letting each consumer own its Environment lifecycle.

Integration colors deliberately avoid the signature_status palette
(emerald/rose/amber/slate) — when both badges render on the same row, they
stay visually distinct.
"""

from jinja2 import Environment

SIGNATURE_STATUS_COLORS: dict[str, str] = {
    "valid": "emerald",
    "invalid": "rose",
    "missing": "amber",
    "no_provider": "slate",
}

INTEGRATION_COLORS: dict[str, str] = {
    "stripe": "violet",
    "github": "zinc",
    "shopify": "green",
    "twilio": "red",
    "mailgun": "sky",
    "discord": "indigo",
    "slack": "yellow",
    "zapier": "orange",
    "n8n": "pink",
}


def apply_globals(env: Environment) -> None:
    """Attach the shared color maps to a Jinja Environment.

    Idempotent — safe to call multiple times.
    """
    env.globals.update(
        signature_status_colors=SIGNATURE_STATUS_COLORS,
        integration_colors=INTEGRATION_COLORS,
    )
