"""Heuristic detection of webhook source from headers + UA + form params.

Priority order matters: most-specific signature first to avoid false positives
when a proxy adds extra headers downstream.

LIMITATIONS (intentional, V3-acceptable):
- Header presence alone is sufficient — we DO NOT verify the HMAC signature
  here. A forged stripe-signature header will be labeled as "stripe" in the
  aggregation. The signature_status column carries the authenticity
  signal; the UI must cross-tabulate detection x authentication, not show
  detection alone.
- Zapier detection is User-Agent-based. Custom Zapier setups that override
  UA won't be detected.
"""

from collections.abc import Callable
from typing import Literal

_HeadersDict = dict[str, str]
_FormParams = dict[str, str]

# Single source of truth for the 9 supported integrations.
#
# Tied to migration 0005's CHECK constraint
# (migrations/versions/0005_4113bf01bbf7_integration_detection.py) AND to the
# hmac.factory._VALIDATORS dict. Adding a 10th integration = update the
# Literal below, the migration's enum, AND the validator registry.
type IntegrationName = Literal[
    "stripe",
    "github",
    "shopify",
    "twilio",
    "mailgun",
    "discord",
    "slack",
    "zapier",
    "n8n",
]

# Runtime mirror of the Literal above for membership checks. Kept in lockstep
# manually because Python has no built-in way to derive a frozenset from a
# `type` statement at module-eval time.
INTEGRATION_NAMES: frozenset[str] = frozenset(
    {
        "stripe",
        "github",
        "shopify",
        "twilio",
        "mailgun",
        "discord",
        "slack",
        "zapier",
        "n8n",
    }
)


def _stripe(h: _HeadersDict, _ua: str, _p: _FormParams) -> tuple[str, str | None] | None:
    if "stripe-signature" in h:
        return ("stripe", None)  # event_type from body JSON, handled by caller
    return None


def _github(h: _HeadersDict, _ua: str, _p: _FormParams) -> tuple[str, str | None] | None:
    # Require BOTH event + delivery to avoid false positives from copy-paste
    # / proxies that forward x-github-event without x-github-delivery.
    if "x-github-event" in h and "x-github-delivery" in h:
        return ("github", h.get("x-github-event"))
    return None


def _shopify(h: _HeadersDict, _ua: str, _p: _FormParams) -> tuple[str, str | None] | None:
    if "x-shopify-topic" in h and "x-shopify-shop-domain" in h:
        return ("shopify", h.get("x-shopify-topic"))
    return None


def _twilio(h: _HeadersDict, _ua: str, _p: _FormParams) -> tuple[str, str | None] | None:
    if "x-twilio-signature" in h:
        return ("twilio", None)
    return None


def _mailgun(h: _HeadersDict, _ua: str, p: _FormParams) -> tuple[str, str | None] | None:
    # Mailgun has TWO modes:
    # (a) Modern webhooks: x-mailgun-signature-v2 header
    # (b) Legacy form-encoded: signature + timestamp + token in the form body.
    if "x-mailgun-signature-v2" in h:
        return ("mailgun", None)
    if "signature" in p and "timestamp" in p and "token" in p:
        return ("mailgun", None)
    return None


def _discord(h: _HeadersDict, _ua: str, _p: _FormParams) -> tuple[str, str | None] | None:
    # Dual-key requirement avoids false positive from naked
    # "x-signature-ed25519" elsewhere.
    if "x-signature-ed25519" in h and "x-signature-timestamp" in h:
        return ("discord", None)
    return None


def _slack(h: _HeadersDict, _ua: str, _p: _FormParams) -> tuple[str, str | None] | None:
    if "x-slack-signature" in h and "x-slack-request-timestamp" in h:
        return ("slack", None)
    return None


def _zapier(_h: _HeadersDict, ua: str, _p: _FormParams) -> tuple[str, str | None] | None:
    # UA-only detection. "Webhooks by Zapier" action sends UA="Zapier" or
    # "Zapier/<version>". x-hook-signature is too generic to be a reliable
    # Zapier signal.
    return ("zapier", None) if "zapier" in ua.lower() else None


def _n8n(h: _HeadersDict, _ua: str, _p: _FormParams) -> tuple[str, str | None] | None:
    if "x-n8n-signature" in h:
        return ("n8n", None)
    return None


# Order matters: specific signatures first, UA-based fallback last (Zapier).
_DETECTORS: list[Callable[[_HeadersDict, str, _FormParams], tuple[str, str | None] | None]] = [
    _stripe,
    _github,
    _shopify,
    _twilio,
    _mailgun,
    _discord,
    _slack,
    _n8n,
    _zapier,
]


def detect_integration(
    headers: _HeadersDict,
    user_agent: str,
    form_params: _FormParams,
) -> tuple[str | None, str | None]:
    """Returns (integration_name, event_type) — both may be None.

    `headers` keys are normalized to lowercase. `user_agent` is read separately
    (callers can extract from `headers.get("user-agent", "")`). `form_params`
    is the parsed application/x-www-form-urlencoded body (empty dict if not
    form-encoded).
    """
    h = {k.lower(): v for k, v in headers.items()}
    for det in _DETECTORS:
        result = det(h, user_agent, form_params)
        if result is not None:
            return result
    return (None, None)
