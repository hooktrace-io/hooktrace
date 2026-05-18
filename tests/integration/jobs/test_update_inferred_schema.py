"""Integration tests for UpdateInferredSchema use case.

These tests require a real Postgres instance (testcontainers) and exercise
the full flow: capture → UpdateInferredSchema → schema persisted + drift set
on the request row.

The arq worker is bypassed: the use case is invoked directly with a real
Postgres session. This is intentional — the test pins the domain logic and
the Postgres upsert/advisory-lock semantics, not the arq transport layer.
"""

import pytest
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

    # 5. Verify the request now has schema_drift set
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    item = resp.json()["items"][0]
    assert item["schema_drift"] is not None
    drift = item["schema_drift"]
    assert "data" in drift["added"]  # first capture → all top-level props added
    assert drift["removed"] == []

    # 6. Verify cumulative schema is accessible via schemas endpoint
    # (NOTE: list_schemas endpoint is introduced in a later PR — skip for now)
    # This test focuses on drift being written to the request row.


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

    # 4. Verify second request drift is empty
    resp = await app_client.get(f"/api/endpoints/{token}/requests")
    items = resp.json()["items"]
    second_item = next(i for i in items if i["id"] == req2_id)
    drift = second_item["schema_drift"]
    assert drift is not None
    assert drift["added"] == []
    assert drift["removed"] == []
    assert drift["changed"] == []
