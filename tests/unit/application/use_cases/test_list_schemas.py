"""Unit tests for ListSchemas use case.

Uses in-memory fakes — Docker / testcontainers NOT required.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.fakes import FakeEndpointRepo, FakeSchemaRepository
from webhook_inspector.application.use_cases.list_schemas import ListSchemas
from webhook_inspector.domain.entities.inferred_schema import InferredSchema
from webhook_inspector.domain.exceptions import EndpointNotFoundError


def _make_schema(
    *,
    endpoint_id,
    integration: str = "stripe",
    event_type: str | None = "charge.succeeded",
) -> InferredSchema:
    now = datetime.now(UTC)
    return InferredSchema(
        id=uuid4(),
        endpoint_id=endpoint_id,
        integration=integration,
        event_type=event_type,
        schema_json={"type": "object", "properties": {}},
        sample_count=1,
        version=1,
        last_field_added_at=None,
        created_at=now,
        updated_at=now,
    )


async def test_returns_empty_when_no_schemas():
    """Endpoint exists but has no schemas → empty list."""
    ep_id = uuid4()
    endpoint_repo = FakeEndpointRepo()
    endpoint_repo.add(token="abc", id=ep_id)
    schema_repo = FakeSchemaRepository()

    use_case = ListSchemas(endpoint_repo=endpoint_repo, schema_repo=schema_repo)
    result = await use_case.execute_for_token("abc")

    assert result == []


async def test_returns_schemas_for_endpoint():
    """Endpoint with 2 schemas → both returned, sorted by integration/event_type."""
    ep_id = uuid4()
    other_ep_id = uuid4()
    endpoint_repo = FakeEndpointRepo()
    endpoint_repo.add(token="tok", id=ep_id)

    schema_repo = FakeSchemaRepository()
    s1 = _make_schema(endpoint_id=ep_id, integration="stripe", event_type="charge.succeeded")
    s2 = _make_schema(endpoint_id=ep_id, integration="github", event_type="push")
    # Schema for a different endpoint — must NOT appear in results
    s_other = _make_schema(endpoint_id=other_ep_id, integration="slack", event_type="message")
    await schema_repo.upsert_with_version(s1)
    await schema_repo.upsert_with_version(s2)
    await schema_repo.upsert_with_version(s_other)

    use_case = ListSchemas(endpoint_repo=endpoint_repo, schema_repo=schema_repo)
    result = await use_case.execute_for_token("tok")

    assert len(result) == 2
    # Sorted by (integration, event_type): github < stripe
    assert result[0].integration == "github"
    assert result[1].integration == "stripe"


async def test_raises_for_unknown_token():
    """Token not found → EndpointNotFoundError."""
    endpoint_repo = FakeEndpointRepo()
    schema_repo = FakeSchemaRepository()

    use_case = ListSchemas(endpoint_repo=endpoint_repo, schema_repo=schema_repo)
    with pytest.raises(EndpointNotFoundError):
        await use_case.execute_for_token("nonexistent")
