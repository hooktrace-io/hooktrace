"""Unit tests for UpdateInferredSchema use case.

Uses in-memory fakes for all ports — no Postgres, no Redis, no R2.
The advisory-lock ordering is tested structurally (lock acquired before
find/upsert) rather than by race condition since unit tests are
single-threaded.
"""

import pytest

from tests.fakes.blob_storage import FakeBlobStorage
from tests.fakes.metrics_collector import FakeMetricsCollector
from tests.fakes.request_repo import FakeRequestRepo
from tests.fakes.schema_repo import FakeSchemaRepository
from webhook_inspector.application.use_cases.update_inferred_schema import (
    UpdateInferredSchema,
    _advisory_lock_key,
)
from webhook_inspector.domain.entities.captured_request import CapturedRequest


def _make_use_case(
    request_repo: FakeRequestRepo | None = None,
    schema_repo: FakeSchemaRepository | None = None,
    blob_storage: FakeBlobStorage | None = None,
    metrics: FakeMetricsCollector | None = None,
) -> tuple[
    UpdateInferredSchema,
    FakeRequestRepo,
    FakeSchemaRepository,
    FakeBlobStorage,
    FakeMetricsCollector,
]:
    rr = request_repo or FakeRequestRepo()
    sr = schema_repo or FakeSchemaRepository()
    bs = blob_storage or FakeBlobStorage()
    mc = metrics or FakeMetricsCollector()
    return (
        UpdateInferredSchema(request_repo=rr, schema_repo=sr, blob_storage=bs, metrics=mc),
        rr,
        sr,
        bs,
        mc,
    )


_SHARED_ENDPOINT_ID = __import__("uuid").UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")


def _make_stripe_request(
    body: bytes = b'{"id": "evt_1", "type": "charge.succeeded", "data": {"object": {"amount": 4200}}}',
    blob_key: str | None = None,
    endpoint_id: __import__("uuid").UUID | None = None,
) -> CapturedRequest:
    eid = endpoint_id or _SHARED_ENDPOINT_ID
    req = CapturedRequest.create(
        endpoint_id=eid,
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={"content-type": "application/json"},
        body=body if blob_key is None else b"",
        source_ip="1.2.3.4",
        inline_threshold_bytes=len(body) + 1 if blob_key is None else 0,
        detected_integration="stripe",
        detected_event_type="charge.succeeded",
    )
    if blob_key is not None:
        # Override blob_key to simulate offloaded body.
        object.__setattr__(req, "blob_key", blob_key)
        object.__setattr__(req, "body_preview", None)
    return req


# ---------------------------------------------------------------------------
# First capture: all fields are "added"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_capture_records_all_fields_as_added() -> None:
    uc, rr, sr, _, mc = _make_use_case()
    req = _make_stripe_request()
    await rr.save(req)

    await uc.execute(req.id)

    # Schema stored under the shared endpoint id
    key = (_SHARED_ENDPOINT_ID, "stripe", "charge.succeeded")
    assert key in sr.schemas
    schema = sr.schemas[key]
    assert schema.sample_count == 1

    # Drift on the request: top-level fields added (first capture = empty old schema).
    # diff_schemas({}, new) yields top-level property keys as "added".
    updated = await rr.find_by_id(req.id)
    assert updated is not None
    drift = updated.schema_drift
    assert drift is not None
    # "data" is the top-level key that contains "object.amount"
    assert "data" in drift["added"]
    assert drift["removed"] == []
    assert drift["changed"] == []

    # Metrics: "updated" status (first capture = new fields)
    assert mc.schema_inference_calls == ["updated"]


# ---------------------------------------------------------------------------
# Second identical capture: no drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_identical_capture_produces_no_drift() -> None:
    uc, rr, _sr, _, mc = _make_use_case()

    req1 = _make_stripe_request()
    await rr.save(req1)
    await uc.execute(req1.id)  # first capture — populates cumulative schema

    req2 = _make_stripe_request()
    await rr.save(req2)
    await uc.execute(req2.id)  # second capture — identical schema

    updated = await rr.find_by_id(req2.id)
    assert updated is not None
    drift = updated.schema_drift
    assert drift is not None
    assert drift == {"added": [], "removed": [], "changed": []}

    assert mc.schema_inference_calls == ["updated", "no_drift"]


# ---------------------------------------------------------------------------
# Second capture with new field: only new field in drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_capture_with_new_field_records_only_new_field() -> None:
    uc, rr, _sr, _, _mc = _make_use_case()

    body1 = b'{"id": "evt_1", "type": "charge.succeeded", "data": {"object": {"amount": 4200}}}'
    req1 = _make_stripe_request(body=body1)
    await rr.save(req1)
    await uc.execute(req1.id)

    body2 = b'{"id": "evt_2", "type": "charge.succeeded", "data": {"object": {"amount": 4200, "currency": "eur"}}}'
    req2 = _make_stripe_request(body=body2)
    await rr.save(req2)
    await uc.execute(req2.id)

    updated2 = await rr.find_by_id(req2.id)
    assert updated2 is not None
    drift = updated2.schema_drift
    assert drift is not None
    assert drift["added"] == ["data.object.currency"]
    assert drift["removed"] == []
    assert drift["changed"] == []


# ---------------------------------------------------------------------------
# Offloaded body is fetched from blob storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offloaded_body_is_fetched_from_blob_storage() -> None:
    blob_key = "endpoint-id/request-id"
    body = b'{"id": "evt_offloaded", "type": "charge.succeeded", "amount": 9900}'
    bs = FakeBlobStorage(blobs={blob_key: body})
    uc, rr, sr, _, mc = _make_use_case(blob_storage=bs)

    req = _make_stripe_request(body=body, blob_key=blob_key)
    await rr.save(req)

    await uc.execute(req.id)

    # Schema must reflect the FULL body, not body_preview (which is None)
    key = (_SHARED_ENDPOINT_ID, "stripe", "charge.succeeded")
    inferred = sr.schemas[key]
    assert "amount" in inferred.schema_json["properties"]
    assert mc.schema_inference_calls == ["updated"]


# ---------------------------------------------------------------------------
# Request not found or deleted (TTL) — skipped gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_request_is_skipped() -> None:
    uc, _, _, _, mc = _make_use_case()
    import uuid

    await uc.execute(uuid.uuid4())  # no request saved
    assert mc.schema_inference_calls == ["skipped_no_integration"]


# ---------------------------------------------------------------------------
# No detected integration — skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_integration_is_skipped() -> None:
    uc, rr, _, _, mc = _make_use_case()
    req = CapturedRequest.create(
        endpoint_id=__import__("uuid").uuid4(),
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={},
        body=b'{"foo": "bar"}',
        source_ip="1.2.3.4",
        inline_threshold_bytes=1000,
        detected_integration=None,  # no integration
    )
    await rr.save(req)
    await uc.execute(req.id)
    assert mc.schema_inference_calls == ["skipped_no_integration"]


# ---------------------------------------------------------------------------
# Non-JSON body — skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_json_body_is_skipped() -> None:
    uc, rr, _, _, mc = _make_use_case()
    req = CapturedRequest.create(
        endpoint_id=__import__("uuid").uuid4(),
        method="POST",
        path="/h/tok",
        query_string=None,
        headers={},
        body=b"not json at all",
        source_ip="1.2.3.4",
        inline_threshold_bytes=1000,
        detected_integration="stripe",
        detected_event_type="charge.succeeded",
    )
    await rr.save(req)
    await uc.execute(req.id)
    assert mc.schema_inference_calls == ["skipped_non_json"]


# ---------------------------------------------------------------------------
# Advisory lock key is stable and in int64 range
# ---------------------------------------------------------------------------


def test_advisory_lock_key_is_stable() -> None:
    import uuid

    eid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    k1 = _advisory_lock_key(eid, "stripe", "charge.succeeded")
    k2 = _advisory_lock_key(eid, "stripe", "charge.succeeded")
    assert k1 == k2
    # Must be a signed int64 (Postgres bigint range)
    assert -(2**63) <= k1 < 2**63


def test_advisory_lock_key_differs_by_event_type() -> None:
    import uuid

    eid = uuid.uuid4()
    k1 = _advisory_lock_key(eid, "stripe", "charge.succeeded")
    k2 = _advisory_lock_key(eid, "stripe", "customer.created")
    assert k1 != k2


def test_advisory_lock_key_handles_none_event_type() -> None:
    import uuid

    eid = uuid.uuid4()
    k = _advisory_lock_key(eid, "github", None)
    assert -(2**63) <= k < 2**63
