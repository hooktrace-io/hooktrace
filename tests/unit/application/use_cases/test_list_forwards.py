"""Unit tests for ListForwards use case.

Covers: happy path delegating to repo with filters passed through,
endpoint-missing path raises EndpointNotFoundError.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.fakes import FakeEndpointRepo, FakeForwardRepository
from webhook_inspector.application.use_cases.list_forwards import ListForwards
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


def _forward(endpoint_id, *, status: str = "failed") -> Forward:
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
        final_status_code=500,
        final_error="boom",
        forward_started_at=now,
        forward_completed_at=None,
        created_at=now,
        manual_retry_at=None,
    )


async def test_returns_forwards_from_repo_for_endpoint():
    ep = _endpoint()
    f1 = _forward(ep.id, status="failed")
    f2 = _forward(ep.id, status="dead")
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    await fwd_repo.save(f1)
    await fwd_repo.save(f2)

    uc = ListForwards(endpoint_repo=ep_repo, forward_repo=fwd_repo)

    result = await uc.execute(token=ep.token, statuses=None, limit=50, before_id=None)

    assert {f.id for f in result} == {f1.id, f2.id}


async def test_filters_by_statuses_passed_through_to_repo():
    ep = _endpoint()
    f_failed = _forward(ep.id, status="failed")
    f_dead = _forward(ep.id, status="dead")
    f_succ = _forward(ep.id, status="succeeded")
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    for f in (f_failed, f_dead, f_succ):
        await fwd_repo.save(f)

    uc = ListForwards(endpoint_repo=ep_repo, forward_repo=fwd_repo)

    result = await uc.execute(token=ep.token, statuses=["failed", "dead"], limit=50, before_id=None)

    assert {f.id for f in result} == {f_failed.id, f_dead.id}


async def test_limit_is_applied():
    ep = _endpoint()
    ep_repo = FakeEndpointRepo(seed=ep)
    fwd_repo = FakeForwardRepository()
    for _ in range(5):
        await fwd_repo.save(_forward(ep.id))

    uc = ListForwards(endpoint_repo=ep_repo, forward_repo=fwd_repo)

    result = await uc.execute(token=ep.token, statuses=None, limit=2, before_id=None)

    assert len(result) == 2


async def test_raises_endpoint_not_found_for_unknown_token():
    uc = ListForwards(endpoint_repo=FakeEndpointRepo(), forward_repo=FakeForwardRepository())

    with pytest.raises(EndpointNotFoundError):
        await uc.execute(token="missing", statuses=None, limit=50, before_id=None)
