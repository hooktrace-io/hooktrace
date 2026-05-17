from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


class DiscordValidator(HmacValidator):
    """Discord interaction webhook signature validator.

    Reference: https://discord.com/developers/docs/interactions/overview#preparing-for-interactions

    ALGORITHM: Ed25519 asymmetric signature — NOT HMAC.
    The 'secret' field is Discord's application PUBLIC KEY (hex-encoded, 32 bytes = 64 hex chars).
    This is intentionally called 'secret' for API uniformity, but it is a public key and safe to
    store less carefully than a shared secret. The private key never leaves Discord's servers.

    Headers required:
      X-Signature-Ed25519: <hex_signature>     (64 bytes = 128 hex chars)
      X-Signature-Timestamp: <unix_timestamp_string>

    Signed payload: (timestamp_string + body_string).encode('utf-8')

    NOTE FOR PR1.3+: The DB/API surface stores this as the 'signing_secret' field. Callers must
    be aware that for Discord, this field holds a public key, not a symmetric secret. Consider
    a separate 'signing_public_key' column in a future schema revision if the distinction matters.
    """

    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult:
        sig_hex = headers.get("x-signature-ed25519") or headers.get("X-Signature-Ed25519")
        timestamp = headers.get("x-signature-timestamp") or headers.get("X-Signature-Timestamp")
        if not sig_hex or not timestamp:
            return ValidationResult.MISSING

        try:
            body_str = body.decode("utf-8")
        except UnicodeDecodeError:
            # Discord payloads must be valid UTF-8 to construct the canonical signed
            # payload. A non-UTF-8 body cannot be verified.
            return ValidationResult.INVALID

        try:
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(secret))
            signed_payload = (timestamp + body_str).encode()
            pk.verify(bytes.fromhex(sig_hex), signed_payload)
        except (InvalidSignature, ValueError):
            return ValidationResult.INVALID

        return ValidationResult.VALID
