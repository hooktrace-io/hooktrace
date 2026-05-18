"""Unit tests for HMAC signature validation in CaptureRequest.execute().

These tests confirm that signature_status is correctly derived and stored
regardless of whether a provider is configured. They use local fakes so
Docker / testcontainers are NOT required.
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from tests.fakes.metrics_collector import FakeMetricsCollector
from webhook_inspector.application.use_cases.capture_request import CaptureRequest
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.ports.blob_storage import BlobStorage
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.request_repository import RequestRepository
from webhook_inspector.infrastructure.crypto.secrets import encrypt_secret

# 32-byte AES-256 key — deterministic for test suite reproducibility.
_TEST_KEY = b"\x00" * 32
_SECRET_PLAIN = "whsec_test123"
_SECRET_ENCRYPTED = encrypt_secret(_TEST_KEY, _SECRET_PLAIN)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeEndpointRepo(EndpointRepository):
    def __init__(self, seed: Endpoint | None = None):
        self.saved = [seed] if seed else []
        self.increments: list[UUID] = []

    async def save(self, endpoint):
        self.saved.append(endpoint)

    async def find_by_token(self, token):
        return next((e for e in self.saved if e.token == token), None)

    async def find_by_id(self, endpoint_id):
        return next((e for e in self.saved if e.id == endpoint_id), None)

    async def update(self, endpoint): ...

    async def increment_request_count(self, endpoint_id):
        self.increments.append(endpoint_id)

    async def delete_expired(self) -> int:
        return 0

    async def count_active(self) -> int:
        return len([e for e in self.saved if e is not None and not e.is_expired()])


class FakeRequestRepo(RequestRepository):
    def __init__(self):
        self.saved: list[CapturedRequest] = []

    async def save(self, request):
        self.saved.append(request)

    async def find_by_id(self, request_id):
        return next((r for r in self.saved if r.id == request_id), None)

    async def list_by_endpoint(self, endpoint_id, limit=50, before_id=None, q=None):
        return []

    async def stream_for_export(self, endpoint_id, max_count):
        for r in [x for x in self.saved if x.endpoint_id == endpoint_id][:max_count]:
            yield r

    async def count_by_endpoint(self, endpoint_id):
        return len([r for r in self.saved if r.endpoint_id == endpoint_id])


class FakeBlobStorage(BlobStorage):
    def __init__(self):
        self.puts: dict[str, bytes] = {}

    async def put(self, key, data):
        self.puts[key] = data

    async def get(self, key):
        return self.puts.get(key)


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


def _stripe_signature(body: bytes, secret: str) -> str:
    ts = str(int(time.time()))
    signed = f"{ts}.{body.decode('utf-8')}".encode()
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_capture_with_valid_stripe_signature_records_valid_status():
    """Endpoint with Stripe HMAC config + body signed correctly → 'valid'."""
    body = b'{"id":"evt_1"}'
    header = _stripe_signature(body, _SECRET_PLAIN)

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
    header = _stripe_signature(body, "wrong_secret")

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
