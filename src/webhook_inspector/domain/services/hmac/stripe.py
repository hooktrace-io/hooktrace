import hashlib
import hmac

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


class StripeValidator(HmacValidator):
    """Stripe webhook signature validator.

    Reference: https://docs.stripe.com/webhooks#verify-manually
    Format: Stripe-Signature: t=<ts>,v1=<sha256_hex>[,v1=<another>]
    Signed payload: f"{ts}.{body}".encode()
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult:
        header = headers.get("stripe-signature") or headers.get("Stripe-Signature")
        if not header:
            return ValidationResult.MISSING

        # Stripe-Signature: t=<ts>,v1=<sig1>[,v1=<sig2>...] (multiple v1 during key rotation).
        # Don't dict() the parts — duplicate keys would clobber. Iterate as list of (k, v).
        timestamp: str | None = None
        signatures: list[str] = []
        for chunk in header.split(","):
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "t":
                timestamp = v
            elif k == "v1":
                signatures.append(v)
        if not timestamp or not signatures:
            return ValidationResult.INVALID

        try:
            signed = f"{timestamp}.{body.decode('utf-8')}".encode()
        except UnicodeDecodeError:
            # Stripe payloads are always UTF-8 JSON; a non-UTF-8 body means either
            # forged traffic or a sender bug. Either way we can't compute the
            # canonical signed string, so the signature can't be validated.
            return ValidationResult.INVALID

        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()

        for actual in signatures:
            if hmac.compare_digest(expected, actual):
                return ValidationResult.VALID
        return ValidationResult.INVALID
