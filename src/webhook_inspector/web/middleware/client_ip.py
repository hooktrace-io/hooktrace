"""Extract the real client IP from request headers, taking Fly's HTTPS
proxy headers into account.

Trust model:
- Fly's HTTPS terminator IS our proxy. It sets `Fly-Client-IP` (verified
  by Fly, not spoofable from outside). We trust it.
- `X-Forwarded-For` is set by Fly too, but we only honor the FIRST entry
  (leftmost client). Subsequent entries are also proxy hops if the client
  was itself behind a CDN — fine for rate-limit purposes.
- ``client_host`` is the last-hop socket peer, which under Fly is
  the fly-proxy. Last-resort fallback only.

If the app is ever deployed behind a different proxy or directly exposed,
the trust model changes. Pin the assumption in the docstring + add a
config flag `trust_proxy_headers` later if needed.

Signature note: this function takes a header Mapping + an optional
``client_host`` string rather than a Starlette ``Request``. That lets it
work uniformly from both Starlette route handlers (pass
``request.headers, request.client.host if request.client else None``) and
pure ASGI middleware (pass headers built from ``scope["headers"]`` and
``scope["client"][0]``). Single source of truth, no duplicated logic.
"""

from collections.abc import Mapping


def extract_client_ip(headers: Mapping[str, str], client_host: str | None) -> str:
    fly = headers.get("fly-client-ip")
    if fly:
        return fly.strip()
    fwd = headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    return client_host if client_host else "0.0.0.0"
