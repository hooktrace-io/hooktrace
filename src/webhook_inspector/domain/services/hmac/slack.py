import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


class SlackValidator(HmacValidator):
    """Slack webhook signature validator.

    Reference: https://api.slack.com/authentication/verifying-requests-from-slack
    Format:
      X-Slack-Signature: v0=<sha256_hex>
      X-Slack-Request-Timestamp: <unix_timestamp>
    Signed payload: f"v0:{timestamp}:{body}".encode()
    Algorithm: HMAC-SHA256, hex-encoded, prefixed with 'v0='.
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult:
        sig_header = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
        timestamp = headers.get("x-slack-request-timestamp") or headers.get(
            "X-Slack-Request-Timestamp"
        )
        if not sig_header or not timestamp:
            return ValidationResult.MISSING

        if not sig_header.startswith("v0="):
            return ValidationResult.INVALID

        actual = sig_header[len("v0=") :]
        basestring = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}".encode()
        expected = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()

        if hmac.compare_digest(expected, actual):
            return ValidationResult.VALID
        return ValidationResult.INVALID
