"""Integration tests for UpdateInferredSchema use case.

These tests require a real Postgres instance (testcontainers) and exercise
the full flow: capture → UpdateInferredSchema → schema persisted + drift set
on the request row.

The arq worker is bypassed: the use case is invoked directly with a real
Postgres session. This is intentional — the test pins the domain logic and
the Postgres upsert/advisory-lock semantics, not the arq transport layer.
"""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_inspector.application.use_cases.update_inferred_schema import (
    UpdateInferredSchema,
)
from webhook_inspector.infrastructure.observability.otel_metrics_collector import (
    OtelMetricsCollector,
)
from webhook_inspector.infrastructure.repositories.request_repository import (
    PostgresRequestRepository,
)
from webhook_inspector.infrastructure.repositories.schema_repository import (
    PostgresSchemaRepository,
)
from webhook_inspector.infrastructure.storage.factory import make_blob_storage


async def _run_update_inferred_schema_inline(
    request_id_str: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    blob_storage,
) -> None:
    """Invoke UpdateInferredSchema synchronously (no arq) for testing."""
    import opentelemetry.metrics as otel_metrics

    meter = otel_metrics.get_meter("test-worker")
    metrics = OtelMetricsCollector(meter)

    async with session_factory() as session:
        use_case = UpdateInferredSchema(
            request_repo=PostgresRequestRepository(session),
            schema_repo=PostgresSchemaRepository(session),
            blob_storage=blob_storage,
            metrics=metrics,
        )
        from uuid import UUID

        await use_case.execute(UUID(request_id_str))
        await session.commit()


@pytest.mark.asyncio
async def test_first_capture_builds_schema_and_sets_drift(
    app_client,
    ingestor_client,
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """End-to-end: capture a Stripe webhook → run UpdateInferredSchema inline
    → schema appears in /api/endpoints/{token}/schemas, drift on request row.
    """
    from webhook_inspector.web.ingestor import deps as ing_deps

    blob_storage = make_blob_storage(ing_deps.get_settings())

    # 1. Create endpoint
    resp = await app_client.post("/api/endpoints", json={})
    assert resp.status_code == 200
    token = resp.json()["token"]

    # 2. Send a Stripe webhook (stripe-signature header triggers integration detection)
    body = b'{"id": "evt_1", "type": "charge.succeeded", "data": {"object": {"amount": 4200}}}'
    resp = await ingestor_client.post(
        f"/h/{token}",
        content=body,
        headers={
            "stripe-signature": "t=1,v1=abc",
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 200

    # 3. Get the captured request id
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    items = resp.json()["items"]
    assert len(items) == 1
    request_id = items[0]["id"]

    # 4. Run UpdateInferredSchema synchronously (bypassing arq)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory: async_sessionmaker[AsyncSession] = session_factory

    await _run_update_inferred_schema_inline(
        request_id,
        session_factory=factory,
        blob_storage=blob_storage,
    )

    # 5. Verify the request now has schema_drift set — query DB directly
    # so this test stays self-contained (PR3.4 will add schema_drift to the
    # JSON read surfaces ; we don't want PR3.3 to depend on that wiring).
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT schema_drift FROM requests WHERE id = :id"),
                {"id": UUID(request_id)},
            )
        ).one()
        drift = row.schema_drift
    assert drift is not None
    assert "data" in drift["added"]  # first capture → all top-level props added
    assert drift["removed"] == []


@pytest.mark.asyncio
async def test_second_identical_capture_produces_no_drift(
    app_client,
    ingestor_client,
    session_factory,
    monkeypatch,
) -> None:
    """Second identical capture → schema_drift is all-empty lists."""
    from webhook_inspector.web.ingestor import deps as ing_deps

    blob_storage = make_blob_storage(ing_deps.get_settings())

    # 1. Create endpoint
    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]

    body = b'{"id": "evt_1", "type": "charge.succeeded", "amount": 100}'
    headers = {"stripe-signature": "t=1,v1=abc", "content-type": "application/json"}

    # 2. First capture
    await ingestor_client.post(f"/h/{token}", content=body, headers=headers)
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    req1_id = resp.json()["items"][0]["id"]
    await _run_update_inferred_schema_inline(
        req1_id, session_factory=session_factory, blob_storage=blob_storage
    )

    # 3. Second identical capture
    await ingestor_client.post(f"/h/{token}", content=body, headers=headers)
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    # items are ordered DESC — most recent first
    req2_id = resp.json()["items"][0]["id"]
    await _run_update_inferred_schema_inline(
        req2_id, session_factory=session_factory, blob_storage=blob_storage
    )

    # 4. Verify second request drift is empty — query DB directly to keep
    # this test independent of the PR3.4 read-surface wiring.
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT schema_drift FROM requests WHERE id = :id"),
                {"id": UUID(req2_id)},
            )
        ).one()
        drift = row.schema_drift
    assert drift is not None
    assert drift["added"] == []
    assert drift["removed"] == []
    assert drift["changed"] == []


@pytest.mark.asyncio
async def test_concurrent_captures_same_event_class_serialize_via_advisory_lock(
    app_client,
    ingestor_client,
    session_factory,
) -> None:
    """Two captures for the same (endpoint, integration, event_type) processed
    concurrently must serialize via pg_advisory_xact_lock. Without the lock,
    both workers would read the same old cumulative schema, compute drift
    against an outdated baseline, and clobber each other on upsert.

    With the lock, the second worker waits for the first transaction's commit
    before reading — so the final cumulative reflects BOTH captures and
    sample_count == 2.
    """
    from webhook_inspector.web.ingestor import deps as ing_deps

    blob_storage = make_blob_storage(ing_deps.get_settings())

    resp = await app_client.post("/api/endpoints", json={})
    token = resp.json()["token"]

    headers = {"stripe-signature": "t=1,v1=abc", "content-type": "application/json"}

    # Two distinct bodies that share the same (integration, event_type) but
    # different top-level fields — so a race would produce inconsistent
    # cumulative state.
    body_a = b'{"id": "evt_a", "type": "charge.succeeded", "amount": 100}'
    body_b = b'{"id": "evt_b", "type": "charge.succeeded", "currency": "eur"}'

    await ingestor_client.post(f"/h/{token}", content=body_a, headers=headers)
    await ingestor_client.post(f"/h/{token}", content=body_b, headers=headers)

    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    items = resp.json()["items"]
    assert len(items) == 2
    req_ids = [item["id"] for item in items]

    # Launch the two updates concurrently. The advisory lock should serialize
    # them ; the gather() must complete without deadlock or exception.
    await asyncio.gather(
        _run_update_inferred_schema_inline(
            req_ids[0],
            session_factory=session_factory,
            blob_storage=blob_storage,
        ),
        _run_update_inferred_schema_inline(
            req_ids[1],
            session_factory=session_factory,
            blob_storage=blob_storage,
        ),
    )

    # Verify final cumulative reflects BOTH captures: sample_count == 2 and
    # the schema contains properties from both bodies (amount AND currency).
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT sample_count, schema_json FROM inferred_schemas "
                    "WHERE integration = 'stripe' AND event_type = 'charge.succeeded'"
                )
            )
        ).one()
    assert row.sample_count == 2
    cumulative_props = row.schema_json.get("properties", {})
    assert "amount" in cumulative_props
    assert "currency" in cumulative_props
