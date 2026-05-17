import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


class GithubValidator(HmacValidator):
    """GitHub webhook signature validator.

    Reference: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
    Format: X-Hub-Signature-256: sha256=<hex_digest>
    Signed payload: raw request body.
    Algorithm: HMAC-SHA256, hex-encoded, prefixed with 'sha256='.
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult:
        header = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
        if not header:
            return ValidationResult.MISSING

        if not header.startswith("sha256="):
            return ValidationResult.INVALID

        actual = header[len("sha256=") :]
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        if hmac.compare_digest(expected, actual):
            return ValidationResult.VALID
        return ValidationResult.INVALID
