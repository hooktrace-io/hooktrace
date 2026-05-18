"""Startup helper for validating the secrets encryption key.

Called from both web/app/main.py and web/ingestor/main.py lifespan functions
so a misconfigured key causes a fast deploy failure rather than a 500 on
every request that needs HMAC or forwarding secret operations.
"""

import base64


def _validate_secrets_key(value: str | None) -> bytes | None:
    """Decode and validate the base64-encoded 32-byte AES-256 secrets key.

    Args:
        value: The raw string from Settings.secrets_encryption_key, or None.

    Returns:
        The decoded 32-byte key, or None if value is None (dev mode — HMAC
        features are unavailable but the service still starts).

    Raises:
        ValueError: If value is set but doesn't decode to exactly 32 bytes.
        binascii.Error: If value is not valid base64.
    """
    if value is None:
        return None
    key = base64.b64decode(value)
    if len(key) != 32:
        raise ValueError(
            f"SECRETS_ENCRYPTION_KEY must encode exactly 32 bytes for AES-256-GCM, "
            f"got {len(key)} bytes"
        )
    return key
