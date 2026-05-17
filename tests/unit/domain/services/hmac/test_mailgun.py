import hashlib
import hmac
import urllib.parse

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.mailgun import MailgunValidator


def _make_mailgun_body(
    secret: str, timestamp: str = "1700000000", token: str = "testtoken123"
) -> bytes:
    signed = f"{timestamp}{token}".encode()
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(
        {"timestamp": timestamp, "token": token, "signature": signature}
    ).encode()


def test_mailgun_valid_signature_returns_valid():
    secret = "mailgun_signing_key"
    body = _make_mailgun_body(secret)
    validator = MailgunValidator()
    assert validator.validate(body=body, headers={}, secret=secret) == ValidationResult.VALID


def test_mailgun_wrong_secret_returns_invalid():
    body = _make_mailgun_body("key_one")
    validator = MailgunValidator()
    assert validator.validate(body=body, headers={}, secret="key_two") == ValidationResult.INVALID


def test_mailgun_missing_header_returns_missing():
    # Mailgun: missing means the form fields (timestamp/token/signature) are absent.
    validator = MailgunValidator()
    assert (
        validator.validate(body=b"unrelated=data", headers={}, secret="secret")
        == ValidationResult.MISSING
    )


def test_mailgun_tampered_body_returns_invalid():
    secret = "mailgun_signing_key"
    # Re-encode with a different timestamp so the signature no longer matches.
    tampered = urllib.parse.urlencode(
        {"timestamp": "9999999999", "token": "testtoken123", "signature": "fakehex"}
    ).encode()
    validator = MailgunValidator()
    assert validator.validate(body=tampered, headers={}, secret=secret) == ValidationResult.INVALID
