"""Unit tests for GetForwardStats use case."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.fakes import FakeEndpointRepo, FakeForwardRepository
from webhook_inspector.application.use_cases.get_forward_stats import GetForwardStats
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.exceptions import EndpointNotFoundError


def _endpoint(token: str = "tok-abc") -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token=token,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
    )


def _forward(endpoint_id, *, status: str) -> Forward:
    now = datetime.now(UTC)
    return Forward(
        id=uuid4(),
        request_id=uuid4(),
        endpoint_id=endpoint_id,
        target_url="https://example.com/hook",
        status=status,  # type: ignore[arg-type]
        attempt_count=1,
        last_attempt_at=now,
        next_attempt_at=None,
        final_status_code=None,
        final_error=None,
        forward_started_at=now,
        forward_completed_at=None,
        created_at=now,
        manual_retry_at=None,
    )


async def test_returns_counts_keyed_by_status():
    ep = _endpoint()
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    for status in ("failed", "failed", "dead", "succeeded"):
        await fwd_repo.save(_forward(ep.id, status=status))

    uc = GetForwardStats(endpoint_repo=ep_repo, forward_repo=fwd_repo)

    result = await uc.execute(token=ep.token)

    assert result["failed"] == 2
    assert result["dead"] == 1
    assert result["succeeded"] == 1
    assert result["pending"] == 0
    assert result["in_flight"] == 0
    assert result["abandoned"] == 0


async def test_raises_endpoint_not_found_for_unknown_token():
    uc = GetForwardStats(endpoint_repo=FakeEndpointRepo(), forward_repo=FakeForwardRepository())

    with pytest.raises(EndpointNotFoundError):
        await uc.execute(token="missing")
