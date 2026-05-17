from webhook_inspector.infrastructure.crypto.secrets import decrypt_secret, encrypt_secret


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
    import pytest
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        decrypt_secret(key2, blob)
