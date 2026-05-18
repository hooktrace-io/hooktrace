"""Sanity tests for the InferredSchema entity dataclass."""

from datetime import UTC, datetime
from uuid import uuid4


def test_inferred_schema_instantiation() -> None:
    from webhook_inspector.domain.entities.inferred_schema import InferredSchema

    now = datetime.now(UTC)
    schema = InferredSchema(
        id=uuid4(),
        endpoint_id=uuid4(),
        integration="stripe",
        event_type="payment_intent.created",
        schema_json={"type": "object", "properties": {}},
        sample_count=3,
        version=1,
        last_field_added_at=now,
        created_at=now,
        updated_at=now,
    )
    assert schema.integration == "stripe"
    assert schema.event_type == "payment_intent.created"
    assert schema.sample_count == 3
    assert schema.version == 1


def test_inferred_schema_event_type_nullable() -> None:
    from webhook_inspector.domain.entities.inferred_schema import InferredSchema

    now = datetime.now(UTC)
    schema = InferredSchema(
        id=uuid4(),
        endpoint_id=uuid4(),
        integration="github",
        event_type=None,
        schema_json={},
        sample_count=1,
        version=0,
        last_field_added_at=None,
        created_at=now,
        updated_at=now,
    )
    assert schema.event_type is None
    assert schema.last_field_added_at is None


def test_inferred_schema_schema_json_is_dict() -> None:
    from webhook_inspector.domain.entities.inferred_schema import InferredSchema

    now = datetime.now(UTC)
    payload = {"fields": ["id", "amount"], "nested": {"key": "value"}}
    schema = InferredSchema(
        id=uuid4(),
        endpoint_id=uuid4(),
        integration="shopify",
        event_type=None,
        schema_json=payload,
        sample_count=5,
        version=2,
        last_field_added_at=None,
        created_at=now,
        updated_at=now,
    )
    assert schema.schema_json == payload
