"""Unit tests for _validate_secrets_key startup helper.

This helper is called by the FastAPI lifespan in both web/app/main.py and
web/ingestor/main.py to fail fast when the key is wrong, rather than
returning 500 on every request.
"""

import base64
import binascii

import pytest

from webhook_inspector.web._secrets_key import _validate_secrets_key


def test_returns_32_byte_key_when_valid():
    """A base64-encoded 32-byte key decodes and is returned as bytes."""
    raw = b"\x01" * 32
    encoded = base64.b64encode(raw).decode()
    result = _validate_secrets_key(encoded)
    assert result == raw
    assert len(result) == 32


def test_raises_on_wrong_length():
    """A key that decodes to != 32 bytes raises ValueError at startup."""
    short = base64.b64encode(b"\x01" * 3).decode()  # AAAA — 3 bytes
    with pytest.raises(ValueError, match="32 bytes"):
        _validate_secrets_key(short)


def test_raises_on_invalid_base64():
    """Garbage that isn't valid base64 raises binascii.Error at startup."""
    with pytest.raises(binascii.Error):
        _validate_secrets_key("not-valid-base64!!!")


def test_returns_none_when_key_is_none():
    """None (key not set) returns None — dev-mode, no HMAC features available."""
    assert _validate_secrets_key(None) is None
