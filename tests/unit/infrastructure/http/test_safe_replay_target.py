"""Unit tests for SafeReplayTarget — two-layer SSRF guard + send()."""

import pytest
import respx

from webhook_inspector.domain.ports.http_replay_target import (
    SsrfBlockedError,
    ValidatedTarget,
)
from webhook_inspector.infrastructure.http.safe_replay_target import SafeReplayTarget

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def target() -> SafeReplayTarget:
    return SafeReplayTarget(blocked_host_suffixes=("hooktrace.io",))


# ---------------------------------------------------------------------------
# Layer (a) — parse-time blocks (no DNS lookup needed for IP literals)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # Loopback / private IPv4 literals
        "http://127.0.0.1/x",
        "http://0.0.0.0/x",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "http://169.254.169.254/latest/meta-data/",  # AWS / Azure IMDS
        # Multicast / reserved IPv4
        "http://224.0.0.1/x",
        "http://239.255.255.250/x",
        "http://255.255.255.255/x",
        # IPv6 literals
        "http://[::1]/",
        "http://[::]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://[ff00::1]/",
        "http://[2001:db8::1]/",
        # Non-http(s) schemes
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
        "dict://example.com/",
        # Userinfo (defang)
        "http://user:pass@10.0.0.1/",
        # Non-standard ports
        "http://example.com:25/",
        "http://example.com:22/",
        "http://example.com:6379/",
    ],
)
def test_blocks_static_evil_urls(target: SafeReplayTarget, url: str) -> None:
    with pytest.raises(SsrfBlockedError):
        target.validate(url)


# ---------------------------------------------------------------------------
# Self-pointing block (blocked_host_suffixes)
# ---------------------------------------------------------------------------


def test_blocks_self_pointing(target: SafeReplayTarget) -> None:
    with pytest.raises(SsrfBlockedError):
        target.validate("https://app.hooktrace.io/foo")
    with pytest.raises(SsrfBlockedError):
        target.validate("https://hook.hooktrace.io/h/abc")


# ---------------------------------------------------------------------------
# Layer (b) — DNS-resolved blocks
# ---------------------------------------------------------------------------


def test_blocks_name_that_resolves_to_private(
    target: SafeReplayTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "webhook_inspector.infrastructure.http.safe_replay_target._resolve",
        lambda host: ["10.0.0.5"],
    )
    with pytest.raises(SsrfBlockedError):
        target.validate("https://localhost.example.com/")


def test_blocks_if_any_resolved_ip_is_private(
    target: SafeReplayTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-A record where one address is public, one is private.
    Must reject — the second address could be selected by httpx at connect."""
    monkeypatch.setattr(
        "webhook_inspector.infrastructure.http.safe_replay_target._resolve",
        lambda host: ["1.1.1.1", "10.0.0.5"],
    )
    with pytest.raises(SsrfBlockedError):
        target.validate("https://multi.example.com/")


def test_blocks_gcp_metadata_name(
    target: SafeReplayTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "webhook_inspector.infrastructure.http.safe_replay_target._resolve",
        lambda host: ["169.254.169.254"],
    )
    with pytest.raises(SsrfBlockedError):
        target.validate("https://metadata.google.internal/")


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


def test_validates_public_url(target: SafeReplayTarget, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "webhook_inspector.infrastructure.http.safe_replay_target._resolve",
        lambda host: ["93.184.216.34"],
    )
    result = target.validate("https://example.com/webhook")
    assert result.host == "example.com"
    assert result.port == 443
    assert result.ip == "93.184.216.34"


# ---------------------------------------------------------------------------
# Empty DNS
# ---------------------------------------------------------------------------


def test_blocks_when_dns_returns_no_addresses(
    target: SafeReplayTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "webhook_inspector.infrastructure.http.safe_replay_target._resolve",
        lambda host: [],
    )
    with pytest.raises(SsrfBlockedError, match="no addresses"):
        target.validate("https://void.example.com/")


# ---------------------------------------------------------------------------
# send() tests — uses respx to mock httpx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_returns_status_headers_body(target: SafeReplayTarget) -> None:
    validated = ValidatedTarget(
        url="https://example.com/webhook",
        host="example.com",
        port=443,
        ip="93.184.216.34",
    )
    with respx.mock(base_url="https://example.com") as respx_mock:
        respx_mock.post("/webhook").respond(
            status_code=204,
            headers={"X-Custom": "ok"},
            content=b"",
        )
        status, headers, body = await target.send(
            method="POST",
            validated=validated,
            headers={"Content-Type": "application/json"},
            body=b'{"hello":"world"}',
        )
    assert status == 204
    assert headers.get("x-custom") == "ok"
    assert body == b""


@pytest.mark.asyncio
async def test_send_truncates_response_to_max_response_bytes() -> None:
    tgt = SafeReplayTarget(max_response_bytes=64)
    validated = ValidatedTarget(
        url="https://example.com/webhook",
        host="example.com",
        port=443,
        ip="93.184.216.34",
    )
    with respx.mock(base_url="https://example.com") as respx_mock:
        respx_mock.post("/webhook").respond(
            status_code=200,
            content=b"A" * 1024,
        )
        _, _, body = await tgt.send(
            method="POST",
            validated=validated,
            headers={},
            body=b"",
        )
    assert len(body) == 64


@pytest.mark.asyncio
async def test_send_does_not_follow_redirects() -> None:
    """SSRF-critical: a 301 -> private must NOT be followed silently. respx
    returns 301; we assert send() returns the 301 status, not the redirect
    target's response."""
    tgt = SafeReplayTarget()
    validated = ValidatedTarget(
        url="https://example.com/redirect",
        host="example.com",
        port=443,
        ip="93.184.216.34",
    )
    with respx.mock(base_url="https://example.com") as respx_mock:
        respx_mock.post("/redirect").respond(
            status_code=301,
            headers={"Location": "http://10.0.0.1/evil"},
        )
        status, _, _ = await tgt.send(
            method="POST",
            validated=validated,
            headers={},
            body=b"",
        )
    assert status == 301  # NOT 200 — redirect not followed
