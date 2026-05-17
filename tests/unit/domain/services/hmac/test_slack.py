import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.slack import SlackValidator


def _make_slack_signature(secret: str, timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_slack_valid_signature_returns_valid():
    secret = "slack_signing_secret"
    body = b"token=test&payload=%7B%7D"
    timestamp = "1700000000"
    sig = _make_slack_signature(secret, timestamp, body)
    validator = SlackValidator()
    assert (
        validator.validate(
            body=body,
            headers={"x-slack-signature": sig, "x-slack-request-timestamp": timestamp},
            secret=secret,
        )
        == ValidationResult.VALID
    )


def test_slack_wrong_secret_returns_invalid():
    body = b"token=test&payload=%7B%7D"
    timestamp = "1700000000"
    sig = _make_slack_signature("secret_one", timestamp, body)
    validator = SlackValidator()
    assert (
        validator.validate(
            body=body,
            headers={"x-slack-signature": sig, "x-slack-request-timestamp": timestamp},
            secret="secret_two",
        )
        == ValidationResult.INVALID
    )


def test_slack_missing_header_returns_missing():
    validator = SlackValidator()
    assert validator.validate(body=b"x", headers={}, secret="secret") == ValidationResult.MISSING


def test_slack_non_utf8_body_returns_invalid():
    """A body with non-UTF-8 bytes cannot be canonically signed. The
    validator must return INVALID (not raise UnicodeDecodeError).
    """
    secret = "slack_signing_secret"
    body = b"\xff\xfe\xfd"  # invalid UTF-8
    # The decode failure short-circuits to INVALID before HMAC compute.
    validator = SlackValidator()
    result = validator.validate(
        body=body,
        headers={
            "x-slack-signature": "v0=abc",
            "x-slack-request-timestamp": "1700000000",
        },
        secret=secret,
    )
    assert result == ValidationResult.INVALID


def test_slack_tampered_body_returns_invalid():
    secret = "slack_signing_secret"
    body = b"token=test&payload=%7B%7D"
    timestamp = "1700000000"
    sig = _make_slack_signature(secret, timestamp, body)
    validator = SlackValidator()
    assert (
        validator.validate(
            body=b"token=evil&payload=%7B%7D",
            headers={"x-slack-signature": sig, "x-slack-request-timestamp": timestamp},
            secret=secret,
        )
        == ValidationResult.INVALID
    )
