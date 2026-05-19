"""HMAC signature for outbound forwarded webhooks.

Stripe-compatible scheme:
    X-Hooktrace-Signature: t=<unix_seconds>,v1=<hex_sha256>
    v1 = HMAC-SHA256(secret, f"{t}.{body}").hexdigest()

Users verify by:
  1. Parse header, extract t and v1.
  2. Reject if |now - t| > 5 minutes.
  3. Compute HMAC-SHA256 over f"{t}.{body}" with the shared secret.
  4. Constant-time compare to v1.

Public docs page: docs/integrations/verifying-forwards.md (PR13).
"""

import hashlib
import hmac


def sign_forward(*, secret: bytes, timestamp: int, body: bytes) -> str:
    msg = f"{timestamp}.".encode("ascii") + body
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()
