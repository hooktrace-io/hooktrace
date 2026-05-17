import hashlib
import hmac
import time

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.stripe import StripeValidator


def _make_stripe_signature(secret: str, payload: bytes, timestamp: int | None = None) -> str:
    ts = timestamp or int(time.time())
    signed = f"{ts}.{payload.decode('utf-8')}".encode()
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_stripe_valid_signature_returns_valid():
    secret = "whsec_testsecret"
    body = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
    sig = _make_stripe_signature(secret, body)
    validator = StripeValidator()
    assert (
        validator.validate(body=body, headers={"stripe-signature": sig}, secret=secret)
        == ValidationResult.VALID
    )


def test_stripe_wrong_secret_returns_invalid():
    body = b'{"id":"evt_1"}'
    sig = _make_stripe_signature("whsec_one", body)
    validator = StripeValidator()
    assert (
        validator.validate(body=body, headers={"stripe-signature": sig}, secret="whsec_two")
        == ValidationResult.INVALID
    )


def test_stripe_missing_header_returns_missing():
    validator = StripeValidator()
    assert (
        validator.validate(body=b"x", headers={}, secret="whsec_anything")
        == ValidationResult.MISSING
    )


def test_stripe_tampered_body_returns_invalid():
    secret = "whsec_testsecret"
    body = b'{"id":"evt_1"}'
    sig = _make_stripe_signature(secret, body)
    validator = StripeValidator()
    assert (
        validator.validate(body=b'{"id":"evt_2"}', headers={"stripe-signature": sig}, secret=secret)
        == ValidationResult.INVALID
    )


def test_stripe_non_utf8_body_returns_invalid():
    """A body with non-UTF-8 bytes cannot be canonically signed. The
    validator must return INVALID (not raise UnicodeDecodeError).
    """
    secret = "whsec_testsecret"
    body = b"\xff\xfe\xfd"  # invalid UTF-8
    # The decode failure short-circuits to INVALID before HMAC compute.
    validator = StripeValidator()
    result = validator.validate(
        body=body,
        headers={"stripe-signature": "t=1700000000,v1=abc"},
        secret=secret,
    )
    assert result == ValidationResult.INVALID


def test_stripe_multiple_v1_signatures_accept_any_valid():
    # During key rotation, Stripe sends multiple v1= in the same header.
    secret_current = "whsec_current"
    secret_old = "whsec_old"
    body = b'{"id":"evt_1"}'
    ts = "1700000000"
    signed = f"{ts}.{body.decode()}".encode()
    sig_current = hmac.new(secret_current.encode(), signed, hashlib.sha256).hexdigest()
    sig_old = hmac.new(secret_old.encode(), signed, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig_old},v1={sig_current}"
    validator = StripeValidator()
    assert (
        validator.validate(body=body, headers={"stripe-signature": header}, secret=secret_current)
        == ValidationResult.VALID
    )
