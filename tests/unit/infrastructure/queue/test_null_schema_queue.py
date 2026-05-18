"""Tests for NullSchemaQueue — the dev-without-Redis fallback."""

import logging
from uuid import uuid4

import pytest

from webhook_inspector.infrastructure.queue.null_schema_queue import NullSchemaQueue


@pytest.mark.asyncio
async def test_enqueue_no_op_does_not_raise():
    queue = NullSchemaQueue()
    await queue.enqueue(uuid4(), endpoint_id=uuid4(), integration="stripe", event_type=None)
    # No assertion needed beyond "does not raise".


@pytest.mark.asyncio
async def test_enqueue_logs_at_debug_level(caplog):
    queue = NullSchemaQueue()
    with caplog.at_level(logging.DEBUG):
        await queue.enqueue(uuid4(), endpoint_id=uuid4(), integration="stripe", event_type=None)
    # The exact log message format depends on impl ; just verify SOMETHING
    # at DEBUG level was emitted with "schema" or "enqueue" or "request_id".
    assert any(
        "schema" in r.message.lower() or "enqueue" in r.message.lower() for r in caplog.records
    )
