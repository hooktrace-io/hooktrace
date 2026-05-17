import hashlib
import hmac
import urllib.parse

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult

_MAX_BODY_BYTES = 64 * 1024  # 64 KB guard against oversized form bodies


class MailgunValidator(HmacValidator):
    """Mailgun webhook signature validator.

    Reference: https://documentation.mailgun.com/docs/mailgun/user-manual/get-started/#webhooks

    SPECIAL CASE: Mailgun embeds its signature in the POST body (form-encoded), NOT in headers.
    The body must contain fields: timestamp, token, signature.
    Signed payload: f"{timestamp}{token}".encode()
    Algorithm: HMAC-SHA256, hex-encoded.

    The 'headers' argument is accepted for API uniformity but is unused.
    ValidationResult.MISSING is returned when timestamp/token/signature are absent from the body.
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],  # noqa: ARG002  — unused; Mailgun signature lives in body
        secret: str,
    ) -> ValidationResult:
        if len(body) > _MAX_BODY_BYTES:
            return ValidationResult.INVALID

        try:
            parsed = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
        except ValueError:
            return ValidationResult.INVALID

        timestamp_vals = parsed.get("timestamp") or []
        token_vals = parsed.get("token") or []
        signature_vals = parsed.get("signature") or []

        if not timestamp_vals or not token_vals or not signature_vals:
            return ValidationResult.MISSING

        timestamp = timestamp_vals[0]
        token = token_vals[0]
        actual = signature_vals[0]

        signed = f"{timestamp}{token}".encode()
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()

        if hmac.compare_digest(expected, actual):
            return ValidationResult.VALID
        return ValidationResult.INVALID
