from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tests.fakes.metrics_collector import FakeMetricsCollector
from webhook_inspector.application.use_cases.capture_request import (
    CaptureRequest,
    EndpointNotFoundError,
)
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.ports.blob_storage import BlobStorage
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.request_repository import RequestRepository


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
    def __init__(self, fail: bool = False):
        self.puts: dict[str, bytes] = {}
        self.fail = fail

    async def put(self, key, data):
        if self.fail:
            raise RuntimeError("storage down")
        self.puts[key] = data

    async def get(self, key):
        return self.puts.get(key)


def _make_endpoint() -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token="abc",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
    )


async def test_capture_small_body_inline():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(erepo, rrepo, blob, inline_threshold=8192, metrics=FakeMetricsCollector())

    _captured, _endpoint = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"x": "y"},
        body=b"hi",
        source_ip="192.0.2.1",
    )

    assert len(rrepo.saved) == 1
    assert rrepo.saved[0].body_preview == "hi"
    assert rrepo.saved[0].blob_key is None
    assert blob.puts == {}
    assert erepo.increments == [ep.id]


async def test_capture_large_body_uploads_blob():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(erepo, rrepo, blob, inline_threshold=8192, metrics=FakeMetricsCollector())

    big = b"x" * 10000
    captured, _endpoint = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=big,
        source_ip="192.0.2.1",
    )

    assert captured.blob_key is not None
    assert blob.puts[captured.blob_key] == big


async def test_capture_falls_back_when_blob_storage_fails():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage(fail=True)
    uc = CaptureRequest(erepo, rrepo, blob, inline_threshold=8192, metrics=FakeMetricsCollector())

    big = b"x" * 10000
    captured, _endpoint = await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=big,
        source_ip="192.0.2.1",
    )

    # Metadata persisted even though blob failed
    assert len(rrepo.saved) == 1
    assert captured.blob_key is None  # downgraded
    assert captured.body_size == 10000


async def test_capture_unknown_token_raises():
    erepo = FakeEndpointRepo()
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(erepo, rrepo, blob, inline_threshold=8192, metrics=FakeMetricsCollector())

    with pytest.raises(EndpointNotFoundError):
        await uc.execute(
            token="missing",
            method="GET",
            path="/h/missing",
            query_string=None,
            headers={},
            body=b"",
            source_ip="192.0.2.1",
        )


async def test_capture_uppercases_method():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    uc = CaptureRequest(erepo, rrepo, blob, inline_threshold=8192, metrics=FakeMetricsCollector())

    captured, _endpoint = await uc.execute(
        token="abc",
        method="post",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"",
        source_ip="192.0.2.1",
    )

    assert captured.method == "POST"


async def test_capture_request_records_metric():
    ep = _make_endpoint()
    erepo = FakeEndpointRepo(ep)
    rrepo = FakeRequestRepo()
    blob = FakeBlobStorage()
    metrics = FakeMetricsCollector()
    uc = CaptureRequest(
        erepo,
        rrepo,
        blob,
        inline_threshold=8192,
        metrics=metrics,
    )

    await uc.execute(
        token="abc",
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"hi",
        source_ip="192.0.2.1",
    )

    assert len(metrics.captured_calls) == 1
    call = metrics.captured_calls[0]
    assert call.method == "POST"
    assert call.body_offloaded is False
    assert call.body_size == 2
    assert call.duration_seconds >= 0
