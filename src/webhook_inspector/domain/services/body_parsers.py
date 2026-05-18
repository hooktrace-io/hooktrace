"""Bounded body parsers for integration detection. All helpers are
defensive: they return empty / None on any failure, never raise.
"""

import json
from urllib.parse import parse_qs

_PARSE_CAP_BYTES = 8 * 1024


def parse_form_params(body: bytes, content_type: str) -> dict[str, str]:
    """Returns flat dict of form params. Empty dict if not form-encoded,
    body too large, or any decode error.
    """
    if "application/x-www-form-urlencoded" not in content_type.lower():
        return {}
    if len(body) > _PARSE_CAP_BYTES:
        return {}
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    try:
        parsed = parse_qs(decoded, keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items() if v}
    except Exception:  # noqa: BLE001
        return {}


def extract_stripe_event_type(body: bytes) -> str | None:
    """Stripe puts event type in the JSON body field "type". Bounded parse,
    defensive against all malformed input.
    """
    if len(body) > _PARSE_CAP_BYTES:
        return None
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        parsed = json.loads(decoded)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    event_type = parsed.get("type")
    return event_type if isinstance(event_type, str) else None
