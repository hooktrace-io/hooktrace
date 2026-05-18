import base64
import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


class TwilioValidator(HmacValidator):
    """Twilio webhook signature validator (simplified body-only variant).

    Reference: https://www.twilio.com/docs/usage/webhooks/webhooks-security

    LIMITATION: True Twilio validation requires computing HMAC-SHA1 over the full
    request URL concatenated with sorted form-parameter key=value pairs (no separators).
    This validator computes HMAC-SHA1 over the raw body bytes instead, which matches
    when the body IS the canonical form-encoded params string and the validator is called
    without URL context.

    Full URL-aware validation (including the URL component) is V4+ work — it requires
    the ingestor to pass the original request URL through the validation context, which
    is not yet supported.

    The 'secret' field is Twilio's Auth Token.
    Header: X-Twilio-Signature: <base64_hmac_sha1>
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult:
        header = headers.get("x-twilio-signature") or headers.get("X-Twilio-Signature")
        if not header:
            return ValidationResult.MISSING

        expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha1).digest()).decode()

        if hmac.compare_digest(expected, header.strip()):
            return ValidationResult.VALID
        return ValidationResult.INVALID
