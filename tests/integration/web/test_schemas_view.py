"""Integration tests for GET /api/endpoints/{token}/schemas.

Requires Docker (testcontainers Postgres) — CI boots Postgres automatically.
Uses the app_client + ingestor_client + session_factory fixtures from conftest.py.
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
    from uuid import UUID

    import opentelemetry.metrics as otel_metrics

    meter = otel_metrics.get_meter("test-schemas-view")
    metrics = OtelMetricsCollector(meter)

    async with session_factory() as session:
        use_case = UpdateInferredSchema(
            request_repo=PostgresRequestRepository(session),
            schema_repo=PostgresSchemaRepository(session),
            blob_storage=blob_storage,
            metrics=metrics,
        )
        await use_case.execute(UUID(request_id_str))
        await session.commit()


@pytest.mark.asyncio
async def test_schemas_endpoint_returns_empty_initially(app_client):
    """Endpoint with no inferred schemas returns an empty list."""
    resp = await app_client.post("/api/endpoints", json={})
    assert resp.status_code == 201
    token = resp.json()["token"]

    resp = await app_client.get(f"/api/endpoints/{token}/schemas")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_schemas_endpoint_returns_after_inference(
    app_client,
    ingestor_client,
    session_factory,
):
    """Capture a Stripe webhook + run UpdateInferredSchema → GET /schemas returns 1 row."""
    from webhook_inspector.web.ingestor import deps as ing_deps

    blob_storage = make_blob_storage(ing_deps.get_settings())

    # 1. Create endpoint
    resp = await app_client.post("/api/endpoints", json={})
    assert resp.status_code == 201
    token = resp.json()["token"]

    # 2. Send a Stripe webhook
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
    await _run_update_inferred_schema_inline(
        request_id,
        session_factory=session_factory,
        blob_storage=blob_storage,
    )

    # 5. Verify GET /schemas returns 1 row with correct fields
    resp = await app_client.get(f"/api/endpoints/{token}/schemas")
    assert resp.status_code == 200
    schemas = resp.json()
    assert len(schemas) == 1
    s = schemas[0]
    assert s["integration"] == "stripe"
    assert s["event_type"] == "charge.succeeded"
    assert s["sample_count"] == 1
    assert "schema_json" in s
    assert "updated_at" in s


@pytest.mark.asyncio
async def test_schemas_endpoint_404_for_unknown_token(app_client):
    """GET /schemas returns 404 for an unknown token."""
    resp = await app_client.get("/api/endpoints/nonexistent-token-xyz/schemas")
    assert resp.status_code == 404
    assert "endpoint not found" in resp.json()["detail"]
