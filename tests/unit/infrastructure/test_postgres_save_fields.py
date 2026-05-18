"""Assert that PostgresRequestRepository.save() passes detected_integration
and detected_event_type through to the RequestTable row — the CRITICAL
persistence check that unit tests with FakeRepo would silently miss.

This test mocks session.add() and inspects the RequestTable instance
handed to it, avoiding any Postgres connection.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.infrastructure.repositories.request_repository import (
    PostgresRequestRepository,
)


def _make_captured(
    detected_integration: str | None = None,
    detected_event_type: str | None = None,
) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"content-type": "application/json"},
        body_preview='{"type":"payment_intent.created"}',
        body_size=33,
        blob_key=None,
        source_ip="1.2.3.4",
        received_at=datetime.now(UTC),
        signature_status="no_provider",
        detected_integration=detected_integration,
        detected_event_type=detected_event_type,
    )


@pytest.mark.asyncio
async def test_save_persists_detected_integration_and_event_type():
    """RequestTable row passed to session.add() must carry both detection fields."""
    request = _make_captured(
        detected_integration="stripe",
        detected_event_type="payment_intent.created",
    )

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    repo = PostgresRequestRepository(session)
    await repo.save(request)

    # session.add should have been called exactly once
    assert session.add.call_count == 1
    row = session.add.call_args[0][0]  # first positional arg

    assert row.detected_integration == "stripe"
    assert row.detected_event_type == "payment_intent.created"


@pytest.mark.asyncio
async def test_save_persists_none_when_no_integration():
    """When integration is None, RequestTable row must have None (not missing attr)."""
    request = _make_captured(detected_integration=None, detected_event_type=None)

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    repo = PostgresRequestRepository(session)
    await repo.save(request)

    row = session.add.call_args[0][0]
    assert row.detected_integration is None
    assert row.detected_event_type is None


def _make_captured_with_drift(
    schema_drift: dict | None = None,
) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"content-type": "application/json"},
        body_preview='{"type":"charge.created"}',
        body_size=25,
        blob_key=None,
        source_ip="1.2.3.4",
        received_at=datetime.now(UTC),
        signature_status="no_provider",
        schema_drift=schema_drift,
    )


@pytest.mark.asyncio
async def test_save_persists_schema_drift():
    """RequestTable row passed to session.add() must carry schema_drift."""
    drift = {"new_fields": ["amount_details"], "removed_fields": []}
    request = _make_captured_with_drift(schema_drift=drift)

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    repo = PostgresRequestRepository(session)
    await repo.save(request)

    row = session.add.call_args[0][0]
    assert row.schema_drift == drift


@pytest.mark.asyncio
async def test_save_persists_none_schema_drift():
    """When schema_drift is None, RequestTable row must have None."""
    request = _make_captured_with_drift(schema_drift=None)

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    repo = PostgresRequestRepository(session)
    await repo.save(request)

    row = session.add.call_args[0][0]
    assert row.schema_drift is None


def _make_captured_with_trace_summary(
    trace_summary: list[dict[str, Any]] | None = None,
) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={"content-type": "application/json"},
        body_preview='{"event":"test"}',
        body_size=16,
        blob_key=None,
        source_ip="1.2.3.4",
        received_at=datetime.now(UTC),
        signature_status="no_provider",
        trace_summary=trace_summary,
    )


@pytest.mark.asyncio
async def test_save_persists_trace_summary():
    """RequestTable row passed to session.add() must carry trace_summary."""
    summary = [{"name": "capture", "duration_ms": 12, "attributes": {}}]
    request = _make_captured_with_trace_summary(trace_summary=summary)

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    repo = PostgresRequestRepository(session)
    await repo.save(request)

    row = session.add.call_args[0][0]
    assert row.trace_summary == summary


@pytest.mark.asyncio
async def test_save_persists_none_trace_summary():
    """When trace_summary is None, RequestTable row must have None."""
    request = _make_captured_with_trace_summary(trace_summary=None)

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    repo = PostgresRequestRepository(session)
    await repo.save(request)

    row = session.add.call_args[0][0]
    assert row.trace_summary is None
