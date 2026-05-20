"""Headers that must be stripped when relaying a captured webhook outbound.

These are either hop-by-hop (Connection, Keep-Alive, ...) or context-specific
to the original inbound capture (Stripe-Signature, Host, X-Forwarded-*, ...)
and must not leak into the forwarded/replayed request.

Defined as a frozenset for O(1) membership; used by both ExecuteForward
(forward feature, PR7) and ReplayRequest (replay feature, PR4). The list was
duplicated byte-for-byte across the two use cases before this module existed
— a single source of truth here makes adding a new sender's signature header
a one-line change instead of two.
"""

HEADERS_TO_STRIP_FROM_CAPTURED: frozenset[str] = frozenset(
    {
        # RFC 7230 §6.1 hop-by-hop headers.
        "connection",
        "keep-alive",
        "te",
        "trailers",
        "upgrade",
        "transfer-encoding",
        "proxy-authenticate",
        "proxy-authorization",
        # Content-Length is recomputed by httpx based on the bytes we pass.
        "content-length",
        # Host is rewritten by httpx based on the target URL.
        "host",
        # Inbound auth/cookies must not leak outbound.
        "authorization",
        "cookie",
        "set-cookie",
        # Sender HMAC signature headers — signed against the original
        # body+timestamp; will NOT verify against our re-signed forward.
        "stripe-signature",
        "x-hub-signature-256",
        "x-shopify-hmac-sha256",
        "x-twilio-signature",
        "x-slack-signature",
        "x-zoom-signature",
    }
)
