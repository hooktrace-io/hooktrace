import base64
import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


class ShopifyValidator(HmacValidator):
    """Shopify webhook signature validator.

    Reference: https://shopify.dev/docs/apps/build/webhooks/subscribe/https#verify-the-webhook
    Format: X-Shopify-Hmac-Sha256: <base64_digest>
    Signed payload: raw request body.
    Algorithm: HMAC-SHA256, base64-encoded (NOT hex).
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult:
        header = headers.get("x-shopify-hmac-sha256") or headers.get("X-Shopify-Hmac-Sha256")
        if not header:
            return ValidationResult.MISSING

        expected = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()

        if hmac.compare_digest(expected, header.strip()):
            return ValidationResult.VALID
        return ValidationResult.INVALID
