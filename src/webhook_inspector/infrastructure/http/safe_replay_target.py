"""HTTP target with parse-time SSRF validation. NOTE: this does NOT mitigate
DNS rebinding — httpx performs its own DNS lookup at connect time and we
do NOT bind to the validated IP in V3. The validate-time resolution is
used only to reject URLs whose hostname CURRENTLY resolves to private
addresses (catches typos + naive attacks, fails against attacker-
controlled DNS rebinding). See PR4 §"DNS rebinding — accepted V3 gap"
for the V4 fix options.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from webhook_inspector.domain.ports.http_replay_target import (
    HttpReplayTarget,
    HttpRequestFailedError,
    SsrfBlockedError,
    ValidatedTarget,
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_PORTS = frozenset({80, 443})

# --- Default config shared between web replay route + worker forward job ----
# Both paths run untrusted user URLs through the same SSRF guard with the
# same timeout and the same response cap. Keeping the values in one place
# stops them from drifting.
_DEFAULT_BLOCKED_HOST_SUFFIXES: tuple[str, ...] = ("hooktrace.io",)
_DEFAULT_TIMEOUT_SECONDS: float = 10.0
_DEFAULT_MAX_RESPONSE_BYTES: int = 256 * 1024


def _resolve(host: str) -> list[str]:
    """Thin wrapper around getaddrinfo so tests can monkeypatch."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_private_or_reserved(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


class SafeReplayTarget(HttpReplayTarget):
    def __init__(
        self,
        blocked_host_suffixes: tuple[str, ...] = (),
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 256 * 1024,
    ) -> None:
        self._blocked_suffixes = tuple(s.lower() for s in blocked_host_suffixes)
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def validate(self, url: str) -> ValidatedTarget:
        parts = urlsplit(url)
        if parts.scheme.lower() not in _ALLOWED_SCHEMES:
            raise SsrfBlockedError(f"scheme not allowed: {parts.scheme}")
        if parts.username or parts.password:
            raise SsrfBlockedError("userinfo not allowed")
        host = parts.hostname
        if not host:
            raise SsrfBlockedError("missing host")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in _ALLOWED_PORTS:
            raise SsrfBlockedError(f"port not allowed: {port}")
        # Strip trailing dot before suffix comparison: "app.hooktrace.io." is
        # the same domain as "app.hooktrace.io" but would otherwise slip the
        # suffix check.
        host_normalized = host.rstrip(".").lower()
        for suffix in self._blocked_suffixes:
            if host_normalized == suffix or host_normalized.endswith("." + suffix):
                raise SsrfBlockedError(f"host suffix blocked: {host}")

        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            ips = _resolve(host)

        if not ips:
            raise SsrfBlockedError(f"DNS returned no addresses for {host}")
        for ip in ips:
            if _is_private_or_reserved(ip):
                raise SsrfBlockedError(f"resolved to private/reserved IP: {ip}")

        return ValidatedTarget(url=url, host=host, port=port, ip=ips[0])

    async def send(
        self,
        *,
        method: str,
        validated: ValidatedTarget,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        """Issue the HTTP call. Returns (status_code, response_headers,
        response_body) with body truncated to max_response_bytes.

        NOTE re: DNS rebinding: httpx performs its OWN DNS resolution at
        connect time. validate()'s resolution is used only to filter out
        private/reserved IPs at parse time — the IP the connection actually
        lands on can differ if the DNS record changes mid-call.

        follow_redirects=False is MANDATORY: a public URL can 301 to a
        private one, bypassing the entire SSRF guard. Do not flip without
        re-validating each redirect hop.
        """
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=False,
                limits=httpx.Limits(max_connections=10),
            ) as client:
                resp = await client.request(
                    method=method,
                    url=validated.url,
                    headers=headers,
                    content=body,
                )
                body_out = bytearray()
                async for chunk in resp.aiter_bytes(chunk_size=8192):
                    body_out.extend(chunk)
                    if len(body_out) >= self._max_response_bytes:
                        body_out = body_out[: self._max_response_bytes]
                        break
                return resp.status_code, dict(resp.headers), bytes(body_out)
        except httpx.TimeoutException as exc:
            # Translate httpx's exception hierarchy into a port-level error
            # so callers in the application layer never import httpx.
            raise HttpRequestFailedError(
                f"{type(exc).__name__}: {exc}",
                kind="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise HttpRequestFailedError(
                f"{type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        except OSError as exc:
            raise HttpRequestFailedError(
                f"{type(exc).__name__}: {exc}",
                kind="network",
            ) from exc


def make_safe_replay_target() -> SafeReplayTarget:
    """Default-configured SafeReplayTarget for outbound replay + forward.

    Centralized so the timeout, response cap, and blocked-host suffixes stay
    in sync between the web replay route (get_replay_request) and the worker
    forward job (execute_forward). Callers that need different values can
    still instantiate `SafeReplayTarget(...)` directly.
    """
    return SafeReplayTarget(
        blocked_host_suffixes=_DEFAULT_BLOCKED_HOST_SUFFIXES,
        timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes=_DEFAULT_MAX_RESPONSE_BYTES,
    )
