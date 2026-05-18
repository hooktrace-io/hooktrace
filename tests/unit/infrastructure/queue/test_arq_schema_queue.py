"""Unit tests for ArqSchemaQueue.enqueue — assert the arq pool is called
with the exact job name + per-request _job_id contract.

The job_id contract is load-bearing : it MUST be `schema:{request_id}` so
every captured request gets its own job (arq dedupes on identical job_id,
which would silently drop concurrent captures of the same event class).
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from webhook_inspector.infrastructure.queue.arq_schema_queue import ArqSchemaQueue


@pytest.mark.asyncio
async def test_enqueue_calls_pool_with_per_request_job_id():
    pool = AsyncMock()
    queue = ArqSchemaQueue(pool=pool)
    request_id = uuid4()

    await queue.enqueue(
        request_id,
        endpoint_id=uuid4(),
        integration="stripe",
        event_type="charge.succeeded",
    )

    pool.enqueue_job.assert_awaited_once_with(
        "update_inferred_schema",
        str(request_id),
        _job_id=f"schema:{request_id}",
    )


@pytest.mark.asyncio
async def test_enqueue_ignores_optional_routing_args():
    """endpoint_id / integration / event_type are kept for caller readability
    but the impl does NOT pass them to arq — the worker reads them from the
    request row by request_id.
    """
    pool = AsyncMock()
    queue = ArqSchemaQueue(pool=pool)
    request_id = uuid4()

    await queue.enqueue(
        request_id,
        endpoint_id=uuid4(),
        integration="github",
        event_type=None,  # event_type may be None
    )

    call_kwargs = pool.enqueue_job.await_args.kwargs
    assert "endpoint_id" not in call_kwargs
    assert "integration" not in call_kwargs
    assert "event_type" not in call_kwargs


@pytest.mark.asyncio
async def test_enqueue_uses_string_form_of_request_id():
    """arq's job payload requires a str, not a UUID object."""
    pool = AsyncMock()
    queue = ArqSchemaQueue(pool=pool)
    request_id = uuid4()

    await queue.enqueue(
        request_id,
        endpoint_id=uuid4(),
        integration="stripe",
        event_type=None,
    )

    positional_args = pool.enqueue_job.await_args.args
    assert positional_args[0] == "update_inferred_schema"
    assert positional_args[1] == str(request_id)
    assert isinstance(positional_args[1], str)
