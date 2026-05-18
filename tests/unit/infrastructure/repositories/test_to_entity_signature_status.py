"""Regression guard: _to_entity must propagate signature_status from ORM row to entity."""

import uuid
from datetime import UTC, datetime

from webhook_inspector.infrastructure.database.models import RequestTable
from webhook_inspector.infrastructure.repositories.request_repository import _to_entity


def test_to_entity_propagates_signature_status() -> None:
    row = RequestTable(
        id=uuid.uuid4(),
        endpoint_id=uuid.uuid4(),
        method="POST",
        path="/",
        query_string=None,
        headers={},
        body_preview=None,
        body_size=2,
        blob_key=None,
        source_ip="127.0.0.1",
        received_at=datetime.now(UTC),
        signature_status="valid",
    )
    entity = _to_entity(row)
    assert entity.signature_status == "valid"
