"""Unit tests for HMAC signature validation in CaptureRequest.execute().

These tests confirm that signature_status is correctly derived and stored
regardless of whether a provider is configured. They use local fakes so
Docker / testcontainers are NOT required.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tests._helpers.stripe import stripe_signature
from tests.fakes import FakeBlobStorage, FakeEndpointRepo, FakeMetricsCollector, FakeRequestRepo
from webhook_inspector.application.use_cases.capture_request import CaptureRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.infrastructure.crypto.secrets import encrypt_secret

# 32-byte AES-256 key — deterministic for test suite reproducibility.
_TEST_KEY = b"\x00" * 32
_SECRET_PLAIN = "whsec_test123"
_SECRET_ENCRYPTED = encrypt_secret(_TEST_KEY, _SECRET_PLAIN)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_endpoint(
    *,
    signature_provider: str | None = None,
    signature_secret_encrypted: bytes | None = None,
) -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token="tok",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
        signature_provider=signature_provider,
        signature_secret_encrypted=signature_secret_encrypted,
    )


def _make_use_case(endpoint: Endpoint) -> tuple[CaptureRequest, FakeRequestRepo]:
    rrepo = FakeRequestRepo()
    uc = CaptureRequest(
        endpoint_repo=FakeEndpointRepo(endpoint),
        request_repo=rrepo,
        blob_storage=FakeBlobStorage(),
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_TEST_KEY,
    )
    return uc, rrepo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_capture_with_valid_stripe_signature_records_valid_status():
    """Endpoint with Stripe HMAC config + body signed correctly → 'valid'."""
    body = b'{"id":"evt_1"}'
    header = stripe_signature(secret=_SECRET_PLAIN, body=body)

    endpoint = _make_endpoint(
        signature_provider="stripe",
        signature_secret_encrypted=_SECRET_ENCRYPTED,
    )
    uc, rrepo = _make_use_case(endpoint)

    await uc.execute(
        token="tok",
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={"stripe-signature": header},
        body=body,
        source_ip="192.0.2.1",
    )

    assert len(rrepo.saved) == 1
    assert rrepo.saved[0].signature_status == "valid"


async def test_capture_with_invalid_signature_records_invalid_status():
    """Same setup but signed with a different secret → 'invalid'."""
    body = b'{"id":"evt_1"}'
    # Sign with a DIFFERENT secret than what's stored on the endpoint.
    header = stripe_signature(secret="wrong_secret", body=body)

    endpoint = _make_endpoint(
        signature_provider="stripe",
        signature_secret_encrypted=_SECRET_ENCRYPTED,
    )
    uc, rrepo = _make_use_case(endpoint)

    await uc.execute(
        token="tok",
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={"stripe-signature": header},
        body=body,
        source_ip="192.0.2.1",
    )

    assert rrepo.saved[0].signature_status == "invalid"


async def test_capture_no_provider_configured_records_no_provider_status():
    """Endpoint with signature_provider=None → 'no_provider'."""
    endpoint = _make_endpoint()  # no provider
    uc, rrepo = _make_use_case(endpoint)

    await uc.execute(
        token="tok",
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={},
        body=b"hello",
        source_ip="192.0.2.1",
    )

    assert rrepo.saved[0].signature_status == "no_provider"


async def test_capture_missing_header_records_missing_status():
    """Endpoint configured for Stripe, request has NO stripe-signature → 'missing'."""
    endpoint = _make_endpoint(
        signature_provider="stripe",
        signature_secret_encrypted=_SECRET_ENCRYPTED,
    )
    uc, rrepo = _make_use_case(endpoint)

    await uc.execute(
        token="tok",
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={},  # no stripe-signature header
        body=b'{"id":"evt_missing"}',
        source_ip="192.0.2.1",
    )

    assert rrepo.saved[0].signature_status == "missing"


async def test_capture_unknown_validator_falls_back_to_no_provider(monkeypatch):
    """If the endpoint's signature_provider isn't in the factory registry
    (e.g. a new service added to the enum but not yet wired to a validator),
    the use case logs a warning and writes 'no_provider' — never crashes.
    """
    from webhook_inspector.application.use_cases import capture_request as cr_mod

    monkeypatch.setattr(cr_mod, "get_validator", lambda _provider: None)

    now = datetime.now(UTC)
    endpoint = Endpoint(
        id=uuid4(),
        token="test-token",
        created_at=now,
        expires_at=now + timedelta(days=7),
        request_count=0,
        signature_provider="fictional-provider",
        signature_secret_encrypted=b"\x00" * 12 + b"ciphertext",  # any non-None bytes
    )

    endpoint_repo = FakeEndpointRepo(seed=endpoint)
    rrepo = FakeRequestRepo()
    uc = CaptureRequest(
        endpoint_repo=endpoint_repo,
        request_repo=rrepo,
        blob_storage=FakeBlobStorage(),
        inline_threshold=8192,
        metrics=FakeMetricsCollector(),
        secrets_key=_TEST_KEY,
    )

    captured, _endpoint = await uc.execute(
        token="test-token",
        method="POST",
        path="/h/test-token",
        query_string=None,
        headers={"stripe-signature": "irrelevant"},
        body=b"x",
        source_ip="1.2.3.4",
    )
    assert captured.signature_status == "no_provider"


async def test_capture_request_handles_corrupt_secret():
    """Endpoint with stripe provider but corrupt ciphertext → signature_status='invalid'.

    AES-GCM's authentication tag verification raises InvalidTag when the key or
    ciphertext is wrong. The use case must catch it and degrade gracefully rather
    than propagating a 500 to the ingestor.
    """
    # 28 bytes of garbage — too short for a valid AES-GCM ciphertext (12-byte nonce +
    # 16-byte tag minimum) and won't decrypt with any key → guaranteed InvalidTag.
    corrupt_ciphertext = b"\x00" * 28

    endpoint = _make_endpoint(
        signature_provider="stripe",
        signature_secret_encrypted=corrupt_ciphertext,
    )
    uc, rrepo = _make_use_case(endpoint)

    # Must not raise; should complete capture with status 'invalid'.
    await uc.execute(
        token="tok",
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={"stripe-signature": "t=1,v1=abc"},
        body=b'{"id":"evt_corrupt"}',
        source_ip="10.0.0.1",
    )

    assert len(rrepo.saved) == 1
    assert rrepo.saved[0].signature_status == "invalid"
