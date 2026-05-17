import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


class ZapierValidator(HmacValidator):
    """Zapier webhook signature validator (custom HMAC signing scheme).

    CONTEXT: Zapier does not natively sign webhooks. Out of the box, Zap webhook triggers
    send no signature header — only a User-Agent of 'Zapier' identifies the source.
    This validator handles the case where the user has configured a custom signing step
    in their Zap (e.g. using Zapier's Code step or a middleware that adds HMAC signing).

    If no signing is set up on the Zap side, the endpoint should leave signature_provider
    unset (ValidationResult.NO_PROVIDER applies) rather than using this validator.

    Format: X-Hook-Signature: <sha256_hex>
    Signed payload: raw request body.
    Algorithm: HMAC-SHA256, hex-encoded.
    Secret: the hook secret configured in the Zap's signing step.
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult:
        header = headers.get("x-hook-signature") or headers.get("X-Hook-Signature")
        if not header:
            return ValidationResult.MISSING

        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        if hmac.compare_digest(expected, header.strip()):
            return ValidationResult.VALID
        return ValidationResult.INVALID
