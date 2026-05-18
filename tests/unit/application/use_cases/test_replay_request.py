"""Unit tests for ReplayRequest use case.

Covers: inline body, blob body, blob expired, target error, SSRF block,
5xx target error, endpoint not found, request not owned, payload too large,
header stripping (auth + sig + hop-by-hop), include_headers/body=False.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from tests.fakes import (
    FakeBlobStorage,
    FakeEndpointRepo,
    FakeHttpReplayTarget,
    FakeMetricsCollector,
    FakeReplayRepository,
    FakeRequestRepo,
)
from webhook_inspector.application.use_cases.replay_request import (
    MAX_REPLAY_BODY_BYTES,
    ReplayPayloadTooLargeError,
    ReplayRequest,
    RequestNotFoundError,
)
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.http_replay_target import SsrfBlockedError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TARGET_URL = "https://example.com/webhook"


def _endpoint(token: str = "tok-abc") -> Endpoint:
    ep_id = uuid4()
    return Endpoint(
        id=ep_id,
        token=token,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
    )


def _captured(
    endpoint_id,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    body_preview: str | None = "hello world",
    blob_key: str | None = None,
) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=endpoint_id,
        method=method,
        path="/hook",
        query_string=None,
        headers=headers if headers is not None else {"Content-Type": "application/json"},
        body_preview=body_preview,
        body_size=len(body_preview.encode()) if body_preview else 0,
        blob_key=blob_key,
        source_ip="1.2.3.4",
        received_at=datetime.now(UTC),
    )


def _use_case(
    *,
    endpoint_repo: FakeEndpointRepo | None = None,
    request_repo: FakeRequestRepo | None = None,
    replay_repo: FakeReplayRepository | None = None,
    target: FakeHttpReplayTarget | None = None,
    blob_storage: FakeBlobStorage | None = None,
    metrics: FakeMetricsCollector | None = None,
) -> tuple[ReplayRequest, FakeMetricsCollector, FakeHttpReplayTarget, FakeReplayRepository]:
    m = metrics or FakeMetricsCollector()
    t = target or FakeHttpReplayTarget()
    rr = replay_repo or FakeReplayRepository()
    return (
        ReplayRequest(
            endpoint_repo=endpoint_repo or FakeEndpointRepo(),
            request_repo=request_repo or FakeRequestRepo(),
            replay_repo=rr,
            target=t,
            blob_storage=blob_storage or FakeBlobStorage(),
            metrics=m,
        ),
        m,
        t,
        rr,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_replays_inline_body_to_target():
    ep = _endpoint()
    req = _captured(ep.id, body_preview="payload data", blob_key=None)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])

    uc, metrics, target, replay_repo = _use_case(endpoint_repo=ep_repo, request_repo=req_repo)

    replay = await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert replay.status_code == 200
    assert replay.error is None
    assert metrics.replay_attempt_calls == ["success"]
    assert target.last_call is not None
    assert target.last_call.body == b"payload data"
    assert len(replay_repo.saved) == 1


async def test_fetches_offloaded_body_from_blob_storage():
    ep = _endpoint()
    blob_key = f"{ep.id}/some-blob"
    req = _captured(ep.id, body_preview=None, blob_key=blob_key)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    blob = FakeBlobStorage(blobs={blob_key: b"offloaded body bytes"})

    uc, metrics, target, _ = _use_case(
        endpoint_repo=ep_repo, request_repo=req_repo, blob_storage=blob
    )

    await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert target.last_call is not None
    assert target.last_call.body == b"offloaded body bytes"
    assert metrics.replay_attempt_calls == ["success"]


async def test_blob_expired_replays_with_empty_body():
    ep = _endpoint()
    blob_key = f"{ep.id}/expired-blob"
    req = _captured(ep.id, body_preview=None, blob_key=blob_key)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    blob = FakeBlobStorage()  # empty — get returns None

    uc, metrics, target, _ = _use_case(
        endpoint_repo=ep_repo, request_repo=req_repo, blob_storage=blob
    )

    replay = await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert target.last_call is not None
    assert target.last_call.body == b""
    assert replay.status_code == 200
    assert metrics.replay_attempt_calls == ["success"]


async def test_records_target_error_as_failure():
    ep = _endpoint()
    req = _captured(ep.id)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])

    target = FakeHttpReplayTarget()
    target.raise_on_send(httpx.ConnectError("Connection refused"))

    uc, metrics, _, replay_repo = _use_case(
        endpoint_repo=ep_repo, request_repo=req_repo, target=target
    )

    replay = await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert replay.status_code is None
    assert replay.error is not None
    assert "Connection refused" in replay.error
    assert metrics.replay_attempt_calls == ["network_error"]
    assert len(replay_repo.saved) == 1


async def test_records_ssrf_block_as_failure():
    ep = _endpoint()
    req = _captured(ep.id)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])

    target = FakeHttpReplayTarget()
    target.raise_on_validate(SsrfBlockedError("port not allowed: 22"))

    uc, metrics, _, replay_repo = _use_case(
        endpoint_repo=ep_repo, request_repo=req_repo, target=target
    )

    replay = await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert replay.error is not None
    assert replay.error.startswith("SsrfBlockedError:")
    assert "port not allowed: 22" in replay.error
    assert metrics.replay_attempt_calls == ["ssrf_blocked"]
    assert len(replay_repo.saved) == 1


async def test_records_5xx_as_target_error():
    ep = _endpoint()
    req = _captured(ep.id)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])

    target = FakeHttpReplayTarget()
    target.respond(status=500, body=b"Internal Server Error", headers={})

    uc, metrics, _, _ = _use_case(endpoint_repo=ep_repo, request_repo=req_repo, target=target)

    replay = await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert replay.status_code == 500
    assert metrics.replay_attempt_calls == ["target_error"]


async def test_raises_endpoint_not_found_for_unknown_token():
    uc, metrics, _, _ = _use_case()

    with pytest.raises(EndpointNotFoundError):
        await uc.execute(token="nonexistent", request_id=uuid4(), target_url=_TARGET_URL)

    assert metrics.replay_attempt_calls == ["endpoint_not_found"]


async def test_raises_request_not_found_for_unowned_request():
    ep = _endpoint("tok-owner")
    other_ep = _endpoint("tok-other")
    # request belongs to other_ep, not ep
    req = _captured(other_ep.id)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])

    uc, metrics, _, _ = _use_case(endpoint_repo=ep_repo, request_repo=req_repo)

    with pytest.raises(RequestNotFoundError):
        await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert metrics.replay_attempt_calls == ["request_not_found"]


async def test_raises_payload_too_large_when_body_exceeds_cap():
    ep = _endpoint()
    blob_key = f"{ep.id}/large-blob"
    req = _captured(ep.id, body_preview=None, blob_key=blob_key)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    large_body = b"x" * (MAX_REPLAY_BODY_BYTES + 1)
    blob = FakeBlobStorage(blobs={blob_key: large_body})

    uc, metrics, _, _ = _use_case(endpoint_repo=ep_repo, request_repo=req_repo, blob_storage=blob)

    with pytest.raises(ReplayPayloadTooLargeError):
        await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert metrics.replay_attempt_calls == ["payload_too_large"]


async def test_strips_authorization_and_signature_headers():
    ep = _endpoint()
    req = _captured(
        ep.id,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer secret-token",
            "Stripe-Signature": "t=12345,v1=abc",
            "X-Custom-Header": "keep-me",
        },
    )
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])

    uc, _, target, _ = _use_case(endpoint_repo=ep_repo, request_repo=req_repo)

    await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert target.last_call is not None
    sent_headers = target.last_call.headers
    assert "Authorization" not in sent_headers
    assert "Stripe-Signature" not in sent_headers
    assert sent_headers.get("Content-Type") == "application/json"
    assert sent_headers.get("X-Custom-Header") == "keep-me"


async def test_strips_hop_by_hop_headers():
    ep = _endpoint()
    req = _captured(
        ep.id,
        headers={
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "Content-Length": "42",
            "Transfer-Encoding": "chunked",
            "Host": "original-host.example.com",
        },
    )
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])

    uc, _, target, _ = _use_case(endpoint_repo=ep_repo, request_repo=req_repo)

    await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL)

    assert target.last_call is not None
    sent_headers = target.last_call.headers
    assert "Connection" not in sent_headers
    assert "Content-Length" not in sent_headers
    assert "Transfer-Encoding" not in sent_headers
    assert "Host" not in sent_headers
    assert sent_headers.get("Content-Type") == "application/json"


async def test_include_headers_false_sends_empty_dict():
    ep = _endpoint()
    req = _captured(
        ep.id,
        headers={"Content-Type": "application/json", "X-Custom": "value"},
    )
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])

    uc, _, target, _ = _use_case(endpoint_repo=ep_repo, request_repo=req_repo)

    await uc.execute(
        token=ep.token, request_id=req.id, target_url=_TARGET_URL, include_headers=False
    )

    assert target.last_call is not None
    assert target.last_call.headers == {}


async def test_include_body_false_sends_empty_body():
    ep = _endpoint()
    blob_key = f"{ep.id}/some-blob"
    req = _captured(ep.id, body_preview=None, blob_key=blob_key)
    ep_repo = FakeEndpointRepo(seed=ep)
    req_repo = FakeRequestRepo(items=[req])
    # blob has data but should not be fetched
    blob = FakeBlobStorage(blobs={blob_key: b"should not be fetched"})

    uc, _, target, _ = _use_case(endpoint_repo=ep_repo, request_repo=req_repo, blob_storage=blob)

    await uc.execute(token=ep.token, request_id=req.id, target_url=_TARGET_URL, include_body=False)

    assert target.last_call is not None
    assert target.last_call.body == b""
    # blob.puts has no gets — no get call was made (blob still has data)
    assert blob.puts == {blob_key: b"should not be fetched"}
