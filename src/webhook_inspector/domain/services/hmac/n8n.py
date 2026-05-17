import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


class N8nValidator(HmacValidator):
    """n8n webhook signature validator.

    Reference: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/#authentication
    Format: X-N8N-Signature: <sha256_hex>
    Signed payload: raw request body.
    Algorithm: HMAC-SHA256, hex-encoded.
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult:
        header = headers.get("x-n8n-signature") or headers.get("X-N8N-Signature")
        if not header:
            return ValidationResult.MISSING

        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        if hmac.compare_digest(expected, header.strip()):
            return ValidationResult.VALID
        return ValidationResult.INVALID
