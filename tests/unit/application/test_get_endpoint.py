from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.fakes import FakeEndpointRepo
from webhook_inspector.application.use_cases.get_endpoint import (
    EndpointNotFoundError,
    GetEndpoint,
)
from webhook_inspector.domain.entities.endpoint import Endpoint


def _ep() -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token="abc",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
        request_count=0,
    )


async def test_returns_endpoint_for_known_token():
    ep = _ep()
    uc = GetEndpoint(endpoint_repo=FakeEndpointRepo(ep))
    result = await uc.execute(token="abc")
    assert result.id == ep.id
    assert result.token == "abc"


async def test_unknown_token_raises():
    uc = GetEndpoint(endpoint_repo=FakeEndpointRepo(None))
    with pytest.raises(EndpointNotFoundError):
        await uc.execute(token="missing")
