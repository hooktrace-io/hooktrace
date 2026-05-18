"""Stripe signature helper shared across HMAC tests + integration tests."""

import hashlib
import hmac
import time


def stripe_signature(*, secret: str, body: bytes, timestamp: int | None = None) -> str:
    """Compute a valid Stripe-Signature header value (`t=<ts>,v1=<sig>`).

    The signed string is `<timestamp>.<body>` per Stripe's spec ; signature is
    HMAC-SHA256 hex of that string using `secret` as the key. If `timestamp`
    is omitted, uses the current epoch second — sufficient for tests that
    only care about validity, not staleness.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    signed = f"{ts}.{body.decode('utf-8')}".encode()
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"
