import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.n8n import N8nValidator


def _make_n8n_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_n8n_valid_signature_returns_valid():
    secret = "n8n_webhook_secret"
    body = b'{"workflow":"my-workflow","data":{}}'
    sig = _make_n8n_signature(secret, body)
    validator = N8nValidator()
    assert (
        validator.validate(body=body, headers={"x-n8n-signature": sig}, secret=secret)
        == ValidationResult.VALID
    )


def test_n8n_wrong_secret_returns_invalid():
    body = b'{"workflow":"my-workflow"}'
    sig = _make_n8n_signature("secret_one", body)
    validator = N8nValidator()
    assert (
        validator.validate(body=body, headers={"x-n8n-signature": sig}, secret="secret_two")
        == ValidationResult.INVALID
    )


def test_n8n_missing_header_returns_missing():
    validator = N8nValidator()
    assert validator.validate(body=b"x", headers={}, secret="secret") == ValidationResult.MISSING


def test_n8n_tampered_body_returns_invalid():
    secret = "n8n_webhook_secret"
    body = b'{"workflow":"my-workflow"}'
    sig = _make_n8n_signature(secret, body)
    validator = N8nValidator()
    assert (
        validator.validate(
            body=b'{"workflow":"tampered-workflow"}',
            headers={"x-n8n-signature": sig},
            secret=secret,
        )
        == ValidationResult.INVALID
    )
