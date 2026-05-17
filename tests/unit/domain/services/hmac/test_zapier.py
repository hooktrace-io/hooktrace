import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.zapier import ZapierValidator


def _make_zapier_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_zapier_valid_signature_returns_valid():
    secret = "zapier_hook_secret"
    body = b'{"data":"value"}'
    sig = _make_zapier_signature(secret, body)
    validator = ZapierValidator()
    assert (
        validator.validate(body=body, headers={"x-hook-signature": sig}, secret=secret)
        == ValidationResult.VALID
    )


def test_zapier_wrong_secret_returns_invalid():
    body = b'{"data":"value"}'
    sig = _make_zapier_signature("secret_one", body)
    validator = ZapierValidator()
    assert (
        validator.validate(body=body, headers={"x-hook-signature": sig}, secret="secret_two")
        == ValidationResult.INVALID
    )


def test_zapier_missing_header_returns_missing():
    validator = ZapierValidator()
    assert validator.validate(body=b"x", headers={}, secret="secret") == ValidationResult.MISSING


def test_zapier_tampered_body_returns_invalid():
    secret = "zapier_hook_secret"
    body = b'{"data":"value"}'
    sig = _make_zapier_signature(secret, body)
    validator = ZapierValidator()
    assert (
        validator.validate(
            body=b'{"data":"tampered"}',
            headers={"x-hook-signature": sig},
            secret=secret,
        )
        == ValidationResult.INVALID
    )
