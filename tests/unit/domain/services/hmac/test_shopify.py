import base64
import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.shopify import ShopifyValidator


def _make_shopify_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_shopify_valid_signature_returns_valid():
    secret = "shopify_secret"
    body = b'{"id":123,"topic":"orders/create"}'
    sig = _make_shopify_signature(secret, body)
    validator = ShopifyValidator()
    assert (
        validator.validate(body=body, headers={"x-shopify-hmac-sha256": sig}, secret=secret)
        == ValidationResult.VALID
    )


def test_shopify_wrong_secret_returns_invalid():
    body = b'{"id":123}'
    sig = _make_shopify_signature("secret_one", body)
    validator = ShopifyValidator()
    assert (
        validator.validate(body=body, headers={"x-shopify-hmac-sha256": sig}, secret="secret_two")
        == ValidationResult.INVALID
    )


def test_shopify_missing_header_returns_missing():
    validator = ShopifyValidator()
    assert validator.validate(body=b"x", headers={}, secret="secret") == ValidationResult.MISSING


def test_shopify_tampered_body_returns_invalid():
    secret = "shopify_secret"
    body = b'{"id":123}'
    sig = _make_shopify_signature(secret, body)
    validator = ShopifyValidator()
    assert (
        validator.validate(
            body=b'{"id":456}', headers={"x-shopify-hmac-sha256": sig}, secret=secret
        )
        == ValidationResult.INVALID
    )
