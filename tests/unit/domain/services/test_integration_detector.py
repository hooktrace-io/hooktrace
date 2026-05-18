"""Unit tests for domain/services/integration_detector.py."""

from webhook_inspector.domain.services.integration_detector import (
    INTEGRATION_NAMES,
    detect_integration,
)

# ---------------------------------------------------------------------------
# Happy paths — per-service
# ---------------------------------------------------------------------------


def test_stripe_detected_by_signature_header():
    headers = {"stripe-signature": "t=12345,v1=abc"}
    name, event_type = detect_integration(headers, "", {})
    assert name == "stripe"
    assert event_type is None  # caller resolves from body


def test_github_detected_with_event_type():
    headers = {
        "x-github-event": "push",
        "x-github-delivery": "abc-123",
    }
    name, event_type = detect_integration(headers, "", {})
    assert name == "github"
    assert event_type == "push"


def test_shopify_detected_with_topic():
    headers = {
        "x-shopify-topic": "orders/create",
        "x-shopify-shop-domain": "mystore.myshopify.com",
    }
    name, event_type = detect_integration(headers, "", {})
    assert name == "shopify"
    assert event_type == "orders/create"


def test_twilio_detected_by_signature_header():
    headers = {"x-twilio-signature": "ABCDEF1234=="}
    name, event_type = detect_integration(headers, "", {})
    assert name == "twilio"
    assert event_type is None


def test_mailgun_detected_by_modern_header():
    headers = {"x-mailgun-signature-v2": "deadbeef"}
    name, event_type = detect_integration(headers, "", {})
    assert name == "mailgun"
    assert event_type is None


def test_mailgun_detected_by_legacy_form_params():
    """Mailgun legacy mode: signature+timestamp+token in form body."""
    form_params = {"signature": "sig", "timestamp": "1234567890", "token": "tok"}
    name, event_type = detect_integration({}, "", form_params)
    assert name == "mailgun"
    assert event_type is None


def test_discord_detected_by_dual_key_headers():
    headers = {
        "x-signature-ed25519": "aabbcc",
        "x-signature-timestamp": "1234567890",
    }
    name, event_type = detect_integration(headers, "", {})
    assert name == "discord"
    assert event_type is None


def test_slack_detected_by_signature_and_timestamp():
    headers = {
        "x-slack-signature": "v0=abc",
        "x-slack-request-timestamp": "1234567890",
    }
    name, event_type = detect_integration(headers, "", {})
    assert name == "slack"
    assert event_type is None


def test_zapier_detected_by_ua_exact():
    name, event_type = detect_integration({}, "Zapier", {})
    assert name == "zapier"
    assert event_type is None


def test_zapier_detected_by_ua_with_version():
    name, event_type = detect_integration({}, "Zapier/2.0.0", {})
    assert name == "zapier"
    assert event_type is None


def test_n8n_detected_by_signature_header():
    headers = {"x-n8n-signature": "sha256=xyz"}
    name, event_type = detect_integration(headers, "", {})
    assert name == "n8n"
    assert event_type is None


# ---------------------------------------------------------------------------
# Negative / edge cases
# ---------------------------------------------------------------------------


def test_no_integration_returns_none_none():
    """Empty headers, empty UA, empty form params → (None, None)."""
    name, event_type = detect_integration({}, "", {})
    assert name is None
    assert event_type is None


def test_priority_order_stripe_beats_github_when_both_present():
    """Stripe is first in _DETECTORS; if both headers present, stripe wins."""
    headers = {
        "stripe-signature": "t=1,v1=abc",
        "x-github-event": "push",
        "x-github-delivery": "uuid",
    }
    name, _ = detect_integration(headers, "", {})
    assert name == "stripe"


def test_zapier_not_detected_when_ua_is_generic_curl():
    """x-hook-signature alone must not trigger zapier; UA='curl' is not Zapier."""
    headers = {"x-hook-signature": "sha256=whatever"}
    name, _ = detect_integration(headers, "curl/7.88.0", {})
    assert name is None


def test_uppercase_header_keys_normalized():
    """Headers with mixed-case keys must still be detected (Stripe-Signature)."""
    headers = {"Stripe-Signature": "t=1,v1=abc"}
    name, _ = detect_integration(headers, "", {})
    assert name == "stripe"


def test_returned_integration_in_enum():
    """Any detected integration name must be in INTEGRATION_NAMES (binds detector to migration)."""
    test_cases = [
        ({"stripe-signature": "x"}, "", {}),
        ({"x-github-event": "push", "x-github-delivery": "id"}, "", {}),
        ({"x-shopify-topic": "t", "x-shopify-shop-domain": "d"}, "", {}),
        ({"x-twilio-signature": "x"}, "", {}),
        ({"x-mailgun-signature-v2": "x"}, "", {}),
        ({}, "", {"signature": "s", "timestamp": "t", "token": "k"}),
        ({"x-signature-ed25519": "x", "x-signature-timestamp": "t"}, "", {}),
        ({"x-slack-signature": "x", "x-slack-request-timestamp": "t"}, "", {}),
        ({}, "Zapier", {}),
        ({"x-n8n-signature": "x"}, "", {}),
    ]
    for headers, ua, form_params in test_cases:
        name, _ = detect_integration(headers, ua, form_params)
        assert name is not None
        assert name in INTEGRATION_NAMES, f"{name!r} not in INTEGRATION_NAMES"


def test_github_requires_both_headers():
    """x-github-event alone (without x-github-delivery) must NOT match github."""
    headers = {"x-github-event": "push"}
    name, _ = detect_integration(headers, "", {})
    assert name is None


def test_discord_requires_both_headers():
    """x-signature-ed25519 alone must NOT match discord (too generic)."""
    headers = {"x-signature-ed25519": "aabbcc"}
    name, _ = detect_integration(headers, "", {})
    assert name is None
