"""Unit tests for ArqForwardQueue."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from webhook_inspector.infrastructure.queue.arq_forward_queue import ArqForwardQueue

_FID = UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock()
    return pool


@pytest.fixture
def queue(mock_pool: AsyncMock) -> ArqForwardQueue:
    return ArqForwardQueue(pool=mock_pool)


@pytest.mark.asyncio
async def test_enqueue_uses_per_request_job_id(
    queue: ArqForwardQueue, mock_pool: AsyncMock
) -> None:
    """_job_id must be stable for a given forward_id+defer_seconds pair."""
    await queue.enqueue(_FID, defer_seconds=30)

    mock_pool.enqueue_job.assert_called_once_with(
        "execute_forward",
        str(_FID),
        _job_id=f"forward:{_FID}:30",
        _defer_by=30,
    )


@pytest.mark.asyncio
async def test_enqueue_passes_defer_seconds(queue: ArqForwardQueue, mock_pool: AsyncMock) -> None:
    """_defer_by must equal the defer_seconds argument."""
    await queue.enqueue(_FID, defer_seconds=120)

    _, kwargs = mock_pool.enqueue_job.call_args
    assert kwargs["_defer_by"] == 120


@pytest.mark.asyncio
async def test_enqueue_string_form_of_uuid(queue: ArqForwardQueue, mock_pool: AsyncMock) -> None:
    """arq receives the UUID as a plain string, not a UUID object."""
    await queue.enqueue(_FID)

    positional_args = mock_pool.enqueue_job.call_args[0]
    assert positional_args[1] == str(_FID)
    assert isinstance(positional_args[1], str)


@pytest.mark.asyncio
async def test_enqueue_default_defer_is_zero(queue: ArqForwardQueue, mock_pool: AsyncMock) -> None:
    """Default defer_seconds=0 produces _job_id ending in :0."""
    await queue.enqueue(_FID)

    _, kwargs = mock_pool.enqueue_job.call_args
    assert kwargs["_defer_by"] == 0
    assert kwargs["_job_id"] == f"forward:{_FID}:0"


@pytest.mark.asyncio
async def test_different_defer_seconds_produce_different_job_ids(
    queue: ArqForwardQueue, mock_pool: AsyncMock
) -> None:
    """Two scheduling rounds with different delays must not deduplicate each other."""
    await queue.enqueue(_FID, defer_seconds=30)
    await queue.enqueue(_FID, defer_seconds=120)

    calls = mock_pool.enqueue_job.call_args_list
    job_ids = [c[1]["_job_id"] for c in calls]
    assert len(set(job_ids)) == 2, "different defer_seconds must yield different job_ids"


@pytest.mark.asyncio
async def test_aclose_releases_pool(queue: ArqForwardQueue, mock_pool: AsyncMock) -> None:
    """aclose() delegates to the underlying Redis pool — lifespan shutdown
    relies on this to avoid leaking connections.
    """
    await queue.aclose()

    mock_pool.aclose.assert_awaited_once()
