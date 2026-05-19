"""Extract the real client IP from a Starlette request, taking Fly's HTTPS
proxy headers into account.

Trust model:
- Fly's HTTPS terminator IS our proxy. It sets `Fly-Client-IP` (verified
  by Fly, not spoofable from outside). We trust it.
- `X-Forwarded-For` is set by Fly too, but we only honor the FIRST entry
  (leftmost client). Subsequent entries are also proxy hops if the client
  was itself behind a CDN — fine for rate-limit purposes.
- `request.client.host` is the last-hop socket peer, which under Fly is
  the fly-proxy. Last-resort fallback only.

If the app is ever deployed behind a different proxy or directly exposed,
the trust model changes. Pin the assumption in the docstring + add a
config flag `trust_proxy_headers` later if needed.
"""

from starlette.requests import Request


def extract_client_ip(request: Request) -> str:
    fly = request.headers.get("fly-client-ip")
    if fly:
        return fly.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    return request.client.host if request.client else "0.0.0.0"
