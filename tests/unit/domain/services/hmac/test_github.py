import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.github import GithubValidator


def _make_github_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_valid_signature_returns_valid():
    secret = "github_secret"
    body = b'{"action":"push"}'
    sig = _make_github_signature(secret, body)
    validator = GithubValidator()
    assert (
        validator.validate(body=body, headers={"x-hub-signature-256": sig}, secret=secret)
        == ValidationResult.VALID
    )


def test_github_wrong_secret_returns_invalid():
    body = b'{"action":"push"}'
    sig = _make_github_signature("secret_one", body)
    validator = GithubValidator()
    assert (
        validator.validate(body=body, headers={"x-hub-signature-256": sig}, secret="secret_two")
        == ValidationResult.INVALID
    )


def test_github_missing_header_returns_missing():
    validator = GithubValidator()
    assert validator.validate(body=b"x", headers={}, secret="secret") == ValidationResult.MISSING


def test_github_tampered_body_returns_invalid():
    secret = "github_secret"
    body = b'{"action":"push"}'
    sig = _make_github_signature(secret, body)
    validator = GithubValidator()
    assert (
        validator.validate(
            body=b'{"action":"delete"}',
            headers={"x-hub-signature-256": sig},
            secret=secret,
        )
        == ValidationResult.INVALID
    )
