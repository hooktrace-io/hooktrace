import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from tests.fakes import FakeBlobStorage, FakeEndpointRepo
from webhook_inspector.application.use_cases.export_requests import (
    ExportRequests,
    ExportTooLargeError,
)
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import EndpointNotFoundError


def _make_request(*, body_preview: str | None, blob_key: str | None) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=UUID("00000000-0000-0000-0000-000000000001"),
        method="POST",
        path="/",
        query_string=None,
        headers={"content-type": "application/json"},
        body_preview=body_preview,
        body_size=10,
        blob_key=blob_key,
        source_ip="127.0.0.1",
        received_at=datetime.now(UTC),
    )


class FakeReqRepo:
    def __init__(self, rows: list[CapturedRequest], total: int) -> None:
        self._rows = rows
        self._total = total

    async def count_by_endpoint(self, endpoint_id):
        return self._total

    def stream_for_export(self, endpoint_id, max_count) -> AsyncIterator[CapturedRequest]:
        async def _gen():
            for r in self._rows[:max_count]:
                yield r

        return _gen()


async def test_export_raises_when_endpoint_missing():
    use_case = ExportRequests(
        endpoint_repo=FakeEndpointRepo(None),
        request_repo=FakeReqRepo([], total=0),
        blob_storage=FakeBlobStorage({}),
        max_requests=10,
    )
    with pytest.raises(EndpointNotFoundError):
        async for _ in use_case.execute(token="missing"):
            pass


async def test_export_raises_when_over_cap():
    endpoint = Endpoint.create(token="abc", ttl_days=7)
    use_case = ExportRequests(
        endpoint_repo=FakeEndpointRepo(endpoint),
        request_repo=FakeReqRepo([], total=11),
        blob_storage=FakeBlobStorage({}),
        max_requests=10,
    )
    with pytest.raises(ExportTooLargeError):
        async for _ in use_case.execute(token="abc"):
            pass


async def test_export_inlines_body_preview_when_blob_key_is_none():
    endpoint = Endpoint.create(token="abc", ttl_days=7)
    req = _make_request(body_preview="hello", blob_key=None)
    use_case = ExportRequests(
        endpoint_repo=FakeEndpointRepo(endpoint),
        request_repo=FakeReqRepo([req], total=1),
        blob_storage=FakeBlobStorage({}),
        max_requests=10,
    )
    chunks = [c async for c in use_case.execute(token="abc")]
    payload = b"".join(chunks).decode()
    data = json.loads(payload)
    assert data["exported_request_count"] == 1
    assert data["requests"][0]["body"] == "hello"


async def test_export_fetches_body_from_gcs_when_blob_key_set():
    endpoint = Endpoint.create(token="abc", ttl_days=7)
    req = _make_request(body_preview=None, blob_key="abc/xyz")
    use_case = ExportRequests(
        endpoint_repo=FakeEndpointRepo(endpoint),
        request_repo=FakeReqRepo([req], total=1),
        blob_storage=FakeBlobStorage({"abc/xyz": b"large body here"}),
        max_requests=10,
    )
    chunks = [c async for c in use_case.execute(token="abc")]
    data = json.loads(b"".join(chunks).decode())
    assert data["requests"][0]["body"] == "large body here"


async def test_export_envelope_includes_endpoint_metadata():
    endpoint = Endpoint.create(token="my-test", ttl_days=7)
    use_case = ExportRequests(
        endpoint_repo=FakeEndpointRepo(endpoint),
        request_repo=FakeReqRepo([], total=0),
        blob_storage=FakeBlobStorage({}),
        max_requests=10,
    )
    chunks = [c async for c in use_case.execute(token="my-test")]
    data = json.loads(b"".join(chunks).decode())
    assert data["endpoint"]["token"] == "my-test"
    assert "created_at" in data["endpoint"]
    assert "expires_at" in data["endpoint"]
    assert data["endpoint"]["response"]["status_code"] == 200
    assert data["exported_request_count"] == 0
    assert data["requests"] == []
    assert "exported_at" in data
