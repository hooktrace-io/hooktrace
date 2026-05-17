import base64
import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.twilio import TwilioValidator


def _make_twilio_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_twilio_valid_signature_returns_valid():
    secret = "twilio_auth_token"
    body = b"From=%2B15555555555&Body=Hello"
    sig = _make_twilio_signature(secret, body)
    validator = TwilioValidator()
    assert (
        validator.validate(body=body, headers={"x-twilio-signature": sig}, secret=secret)
        == ValidationResult.VALID
    )


def test_twilio_wrong_secret_returns_invalid():
    body = b"From=%2B15555555555&Body=Hello"
    sig = _make_twilio_signature("token_one", body)
    validator = TwilioValidator()
    assert (
        validator.validate(body=body, headers={"x-twilio-signature": sig}, secret="token_two")
        == ValidationResult.INVALID
    )


def test_twilio_missing_header_returns_missing():
    validator = TwilioValidator()
    assert validator.validate(body=b"x", headers={}, secret="secret") == ValidationResult.MISSING


def test_twilio_tampered_body_returns_invalid():
    secret = "twilio_auth_token"
    body = b"From=%2B15555555555&Body=Hello"
    sig = _make_twilio_signature(secret, body)
    validator = TwilioValidator()
    assert (
        validator.validate(
            body=b"From=%2B19999999999&Body=Tampered",
            headers={"x-twilio-signature": sig},
            secret=secret,
        )
        == ValidationResult.INVALID
    )
