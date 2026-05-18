"""Regression guard: endpoint _to_entity must propagate forward fields."""

import uuid
from datetime import UTC, datetime

from webhook_inspector.infrastructure.database.models import EndpointTable
from webhook_inspector.infrastructure.repositories.endpoint_repository import _to_entity


def _base_row(**kwargs: object) -> EndpointTable:
    return EndpointTable(
        id=uuid.uuid4(),
        token="abc123",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        request_count=0,
        response_status_code=200,
        response_body='{"ok":true}',
        response_headers={},
        response_delay_ms=0,
        **kwargs,
    )


def test_to_entity_propagates_forward_url() -> None:
    row = _base_row(forward_url="https://example.com/hook")
    entity = _to_entity(row)
    assert entity.forward_url == "https://example.com/hook"


def test_to_entity_propagates_forward_headers() -> None:
    row = _base_row(forward_headers={"X-Custom": "val"})
    entity = _to_entity(row)
    assert entity.forward_headers == {"X-Custom": "val"}


def test_to_entity_propagates_forward_secret_encrypted() -> None:
    row = _base_row(forward_secret_encrypted=b"secret-bytes")
    entity = _to_entity(row)
    assert entity.forward_secret_encrypted == b"secret-bytes"


def test_to_entity_forward_fields_default_none() -> None:
    row = _base_row()
    entity = _to_entity(row)
    assert entity.forward_url is None
    assert entity.forward_headers is None
    assert entity.forward_secret_encrypted is None
