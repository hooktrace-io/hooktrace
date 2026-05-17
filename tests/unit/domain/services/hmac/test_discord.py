from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.discord import DiscordValidator


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Return (private_key, public_key_hex)."""
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    return private_key, public_bytes.hex()


def _make_discord_signature(private_key: Ed25519PrivateKey, timestamp: str, body: bytes) -> str:
    signed = (timestamp + body.decode("utf-8")).encode()
    sig_bytes = private_key.sign(signed)
    return sig_bytes.hex()


def test_discord_valid_signature_returns_valid():
    private_key, public_key_hex = _generate_keypair()
    body = b'{"type":1}'
    timestamp = "1700000000"
    sig = _make_discord_signature(private_key, timestamp, body)
    validator = DiscordValidator()
    assert (
        validator.validate(
            body=body,
            headers={"x-signature-ed25519": sig, "x-signature-timestamp": timestamp},
            secret=public_key_hex,
        )
        == ValidationResult.VALID
    )


def test_discord_wrong_key_returns_invalid():
    private_key, _ = _generate_keypair()
    _, other_public_key_hex = _generate_keypair()  # different key pair
    body = b'{"type":1}'
    timestamp = "1700000000"
    sig = _make_discord_signature(private_key, timestamp, body)
    validator = DiscordValidator()
    assert (
        validator.validate(
            body=body,
            headers={"x-signature-ed25519": sig, "x-signature-timestamp": timestamp},
            secret=other_public_key_hex,
        )
        == ValidationResult.INVALID
    )


def test_discord_missing_header_returns_missing():
    validator = DiscordValidator()
    assert validator.validate(body=b"x", headers={}, secret="a" * 64) == ValidationResult.MISSING


def test_discord_tampered_body_returns_invalid():
    private_key, public_key_hex = _generate_keypair()
    body = b'{"type":1}'
    timestamp = "1700000000"
    sig = _make_discord_signature(private_key, timestamp, body)
    validator = DiscordValidator()
    assert (
        validator.validate(
            body=b'{"type":2}',
            headers={"x-signature-ed25519": sig, "x-signature-timestamp": timestamp},
            secret=public_key_hex,
        )
        == ValidationResult.INVALID
    )
