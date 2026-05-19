from uuid import UUID

import pytest

from tests.fakes import FakeEndpointRepo, FakeMetricsCollector
from webhook_inspector.application.use_cases.create_endpoint import CreateEndpoint
from webhook_inspector.domain.exceptions import (
    InvalidSlugError,
    ReservedSlugError,
    SlugAlreadyTakenError,
    SlugDenylistedError,
)


async def test_creates_and_persists_endpoint():
    repo = FakeEndpointRepo()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=FakeMetricsCollector())

    result = await use_case.execute()

    assert isinstance(result.id, UUID)
    assert isinstance(result.token, str)
    assert len(repo.saved) == 1
    assert repo.saved[0].token == result.token


async def test_each_call_generates_distinct_token():
    repo = FakeEndpointRepo()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=FakeMetricsCollector())

    a = await use_case.execute()
    b = await use_case.execute()

    assert a.token != b.token


async def test_creates_endpoint_with_custom_response():
    repo = FakeEndpointRepo()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=FakeMetricsCollector())

    result = await use_case.execute(
        response_status_code=201,
        response_body='{"created":true}',
        response_headers={"X-Foo": "bar"},
        response_delay_ms=100,
    )

    assert result.response_status_code == 201
    assert result.response_body == '{"created":true}'
    assert result.response_headers == {"X-Foo": "bar"}
    assert result.response_delay_ms == 100
    assert repo.saved[0].response_status_code == 201


async def test_creates_endpoint_with_default_response_when_unspecified():
    repo = FakeEndpointRepo()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=FakeMetricsCollector())

    result = await use_case.execute()  # no kwargs

    assert result.response_status_code == 200
    assert result.response_body == '{"ok":true}'
    assert result.response_headers == {}
    assert result.response_delay_ms == 0


async def test_create_endpoint_increments_metric():
    repo = FakeEndpointRepo()
    metrics = FakeMetricsCollector()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=metrics)

    await use_case.execute()

    assert metrics.endpoints_created_count == 1


async def test_creates_endpoint_with_user_supplied_slug():
    repo = FakeEndpointRepo()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=FakeMetricsCollector())

    result = await use_case.execute(slug="my-app-webhooks")

    assert result.token == "my-app-webhooks"
    assert repo.saved[0].token == "my-app-webhooks"


async def test_rejects_invalid_slug():
    repo = FakeEndpointRepo()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=FakeMetricsCollector())

    with pytest.raises(InvalidSlugError):
        await use_case.execute(slug="FOO")

    assert repo.saved == []


async def test_rejects_reserved_slug():
    repo = FakeEndpointRepo()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=FakeMetricsCollector())

    with pytest.raises(ReservedSlugError):
        await use_case.execute(slug="api")

    assert repo.saved == []


class _ConflictingRepo(FakeEndpointRepo):
    async def save(self, endpoint):
        raise SlugAlreadyTakenError(f"endpoint with token '{endpoint.token}' already exists")


async def test_propagates_slug_already_taken_from_repo():
    repo = _ConflictingRepo()
    metrics = FakeMetricsCollector()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=metrics)

    with pytest.raises(SlugAlreadyTakenError):
        await use_case.execute(slug="foo")

    assert metrics.endpoints_created_count == 0


async def test_execute_raises_on_denylisted_slug():
    repo = FakeEndpointRepo()
    metrics = FakeMetricsCollector()
    use_case = CreateEndpoint(repo=repo, ttl_days=7, metrics=metrics)
    with pytest.raises(SlugDenylistedError):
        await use_case.execute(slug="my-stripe-clone")
    assert repo.saved == []  # no endpoint persisted on denylist hit
    assert metrics.endpoints_created_count == 0  # no metric emitted
