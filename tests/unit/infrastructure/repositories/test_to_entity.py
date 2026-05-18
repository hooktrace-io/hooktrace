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


def test_to_entity_propagates_integration_fields() -> None:
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
        signature_status="no_provider",
        detected_integration="github",
        detected_event_type="push",
    )
    entity = _to_entity(row)
    assert entity.detected_integration == "github"
    assert entity.detected_event_type == "push"


def test_to_entity_integration_fields_default_none() -> None:
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
        signature_status="no_provider",
    )
    entity = _to_entity(row)
    assert entity.detected_integration is None
    assert entity.detected_event_type is None


def test_to_entity_propagates_schema_drift() -> None:
    drift = {"new_fields": ["amount_details"], "removed_fields": []}
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
        signature_status="no_provider",
        schema_drift=drift,
    )
    entity = _to_entity(row)
    assert entity.schema_drift == drift


def test_to_entity_schema_drift_defaults_none() -> None:
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
        signature_status="no_provider",
    )
    entity = _to_entity(row)
    assert entity.schema_drift is None


def test_to_entity_propagates_trace_summary() -> None:
    summary = [{"name": "capture", "duration_ms": 12, "attributes": {}}]
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
        signature_status="no_provider",
        trace_summary=summary,
    )
    entity = _to_entity(row)
    assert entity.trace_summary == summary


def test_to_entity_trace_summary_defaults_none() -> None:
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
        signature_status="no_provider",
    )
    entity = _to_entity(row)
    assert entity.trace_summary is None
