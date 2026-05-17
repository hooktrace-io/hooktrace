import pytest
from cryptography.exceptions import InvalidTag

from webhook_inspector.infrastructure.crypto.secrets import (
    decrypt_secret,
    encrypt_secret,
)


def test_encrypt_decrypt_roundtrip():
    key = b"\x00" * 32  # 32 bytes for AES-256-GCM
    plaintext = "whsec_test_stripe_signing_secret"
    blob = encrypt_secret(key, plaintext)
    assert isinstance(blob, bytes)
    assert blob != plaintext.encode()  # actually encrypted
    out = decrypt_secret(key, blob)
    assert out == plaintext


def test_decrypt_with_wrong_key_raises():
    key1 = b"\x00" * 32
    key2 = b"\x01" * 32
    blob = encrypt_secret(key1, "secret")
    with pytest.raises(InvalidTag):
        decrypt_secret(key2, blob)


@pytest.mark.parametrize("wrong_size", [0, 15, 16, 24, 31, 33, 64])
def test_encrypt_rejects_non_32_byte_key(wrong_size):
    key = b"\x00" * wrong_size
    with pytest.raises(ValueError, match="32 bytes"):
        encrypt_secret(key, "secret")


@pytest.mark.parametrize("wrong_size", [0, 15, 16, 24, 31, 33, 64])
def test_decrypt_rejects_non_32_byte_key(wrong_size):
    # Build a valid blob with a correct key first, then try to decrypt with wrong-sized key.
    correct_key = b"\x00" * 32
    blob = encrypt_secret(correct_key, "secret")
    wrong_key = b"\x00" * wrong_size
    with pytest.raises(ValueError, match="32 bytes"):
        decrypt_secret(wrong_key, blob)


def test_decrypt_rejects_blob_too_short():
    """The blob must contain at least the 12-byte nonce. A blob shorter than
    that can never have been produced by encrypt_secret, so we fail fast with
    a clear ValueError instead of letting AESGCM raise an opaque error.
    """
    key = b"\x00" * 32
    with pytest.raises(ValueError, match="blob too short"):
        decrypt_secret(key, b"too-short")  # 9 bytes < 12
