"""AES-256-GCM helpers for encrypting at-rest secrets (HMAC secrets, Forward secrets).

Format of ciphertext blob: nonce (12 bytes) || ciphertext+tag (variable).
"""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12


def encrypt_secret(key: bytes, plaintext: str) -> bytes:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes for AES-256-GCM")
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return nonce + ciphertext


def decrypt_secret(key: bytes, blob: bytes) -> str:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes for AES-256-GCM")
    if len(blob) < _NONCE_SIZE:
        raise ValueError("blob too short")
    aesgcm = AESGCM(key)
    nonce, ciphertext = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return aesgcm.decrypt(nonce, ciphertext, associated_data=None).decode("utf-8")
