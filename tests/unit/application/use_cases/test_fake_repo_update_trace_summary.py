"""Verify FakeRequestRepo.update_trace_summary mutates the in-memory entity."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.fakes.request_repo import FakeRequestRepo
from webhook_inspector.domain.entities.captured_request import CapturedRequest


def _make_request(trace_summary=None) -> CapturedRequest:
    return CapturedRequest(
        id=uuid4(),
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body_preview="{}",
        body_size=2,
        blob_key=None,
        source_ip="1.2.3.4",
        received_at=datetime.now(UTC),
        trace_summary=trace_summary,
    )


@pytest.mark.asyncio
async def test_fake_repo_update_trace_summary_mutates_in_memory() -> None:
    repo = FakeRequestRepo()
    req = _make_request()
    await repo.save(req)

    summary = [{"name": "capture", "duration_ms": 5, "attributes": {"method": "POST"}}]
    await repo.update_trace_summary(req.id, summary)

    updated = await repo.find_by_id(req.id)
    assert updated is not None
    assert updated.trace_summary == summary


@pytest.mark.asyncio
async def test_fake_repo_update_trace_summary_unknown_id_is_noop() -> None:
    """update_trace_summary with an unknown id should not raise."""
    repo = FakeRequestRepo()
    await repo.update_trace_summary(uuid4(), [{"name": "x"}])
    assert repo.saved == []
