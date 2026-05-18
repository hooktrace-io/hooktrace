"""Unit tests for the outbound HMAC signature scheme."""

import hashlib
import hmac

from webhook_inspector.application.use_cases.outbound_signature import sign_forward


def test_signature_matches_stripe_style_verification() -> None:
    secret = b"whsec_test"
    body = b'{"hello":"world"}'
    ts = 1716000000

    sig = sign_forward(secret=secret, timestamp=ts, body=body)

    # Verifier (what a user will write):
    expected = hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_signature_changes_when_body_changes() -> None:
    secret = b"whsec_test"
    ts = 1716000000
    s1 = sign_forward(secret=secret, timestamp=ts, body=b"a")
    s2 = sign_forward(secret=secret, timestamp=ts, body=b"b")
    assert s1 != s2


def test_signature_changes_when_timestamp_changes() -> None:
    secret = b"whsec_test"
    body = b'{"event":"test"}'
    s1 = sign_forward(secret=secret, timestamp=1716000000, body=body)
    s2 = sign_forward(secret=secret, timestamp=1716000001, body=body)
    assert s1 != s2


def test_signature_deterministic_for_same_inputs() -> None:
    secret = b"some_secret_key"
    body = b"payload"
    ts = 1716000000
    s1 = sign_forward(secret=secret, timestamp=ts, body=body)
    s2 = sign_forward(secret=secret, timestamp=ts, body=body)
    assert s1 == s2


def test_signature_is_hex_string() -> None:
    sig = sign_forward(secret=b"key", timestamp=1000, body=b"body")
    # SHA-256 hex digest is always 64 hex characters
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)


def test_empty_body_still_produces_valid_signature() -> None:
    secret = b"whsec_empty"
    ts = 1716000000
    sig = sign_forward(secret=secret, timestamp=ts, body=b"")
    expected = hmac.new(secret, f"{ts}.".encode(), hashlib.sha256).hexdigest()
    assert sig == expected
