"""Unit tests for ListIntegrations use case.

Uses in-memory fakes — Docker / testcontainers NOT required.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from webhook_inspector.application.use_cases.list_integrations import ListIntegrations
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.entities.integration_aggregate import IntegrationAggregate
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.request_repository import RequestRepository

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeEndpointRepo(EndpointRepository):
    def __init__(self):
        self._endpoints: list[Endpoint] = []

    def add(self, *, token: str, id: str | UUID) -> Endpoint:
        ep_id = UUID(str(id)) if isinstance(id, str) else id
        ep = Endpoint(
            id=ep_id,
            token=token,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            request_count=0,
        )
        self._endpoints.append(ep)
        return ep

    async def save(self, endpoint): ...

    async def find_by_token(self, token):
        return next((e for e in self._endpoints if e.token == token), None)

    async def find_by_id(self, endpoint_id):
        return next((e for e in self._endpoints if e.id == endpoint_id), None)

    async def update(self, endpoint): ...

    async def increment_request_count(self, endpoint_id): ...

    async def delete_expired(self) -> int:
        return 0

    async def count_active(self) -> int:
        return 0


class FakeRequestRepo(RequestRepository):
    """In-memory request repo with full aggregate_by_integration logic."""

    def __init__(self):
        self._items: list[dict] = []

    def add(
        self,
        *,
        endpoint_id: str | UUID,
        integration: str,
        event: str | None = None,
        signature_status: str = "no_provider",
    ) -> None:
        self._items.append(
            {
                "endpoint_id": UUID(str(endpoint_id))
                if isinstance(endpoint_id, str)
                else endpoint_id,
                "integration": integration,
                "event": event,
                "signature_status": signature_status,
            }
        )

    async def save(self, request): ...

    async def find_by_id(self, request_id):
        return None

    async def list_by_endpoint(self, endpoint_id, limit=50, before_id=None, q=None):
        return []

    async def stream_for_export(self, endpoint_id, max_count):
        return
        yield  # make it a generator

    async def count_by_endpoint(self, endpoint_id):
        return 0

    async def aggregate_by_integration(self, endpoint_id: UUID) -> list[IntegrationAggregate]:
        """In-memory aggregation matching the PostgreSQL 3-CTE behaviour."""
        relevant = [
            item
            for item in self._items
            if item["endpoint_id"] == endpoint_id and item["integration"] is not None
        ]

        # Group by integration
        integrations: dict[str, dict] = {}
        for item in relevant:
            key = item["integration"]
            if key not in integrations:
                integrations[key] = {"total": 0, "event_types": {}, "signature_status_counts": {}}
            integrations[key]["total"] += 1
            # event_types
            if item["event"] is not None:
                et = item["event"]
                integrations[key]["event_types"][et] = (
                    integrations[key]["event_types"].get(et, 0) + 1
                )
            # signature_status_counts
            ss = item["signature_status"]
            integrations[key]["signature_status_counts"][ss] = (
                integrations[key]["signature_status_counts"].get(ss, 0) + 1
            )

        # Sort by total DESC (matches ORDER BY pi.total DESC in SQL)
        return [
            IntegrationAggregate(
                integration=k,
                total=v["total"],
                event_types=v["event_types"],
                signature_status_counts=v["signature_status_counts"],
            )
            for k, v in sorted(integrations.items(), key=lambda x: -x[1]["total"])
        ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def endpoint_repo():
    return FakeEndpointRepo()


@pytest.fixture
def request_repo():
    return FakeRequestRepo()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_aggregates_by_integration_event_and_signature_status(endpoint_repo, request_repo):
    """Core aggregation: totals, event_types, signature_status_counts all correct."""
    ep_id = uuid4()
    endpoint_repo.add(token="abc", id=ep_id)
    request_repo.add(
        endpoint_id=ep_id, integration="stripe", event="charge.succeeded", signature_status="valid"
    )
    request_repo.add(
        endpoint_id=ep_id, integration="stripe", event="charge.succeeded", signature_status="valid"
    )
    request_repo.add(
        endpoint_id=ep_id, integration="stripe", event="charge.refunded", signature_status="invalid"
    )
    request_repo.add(
        endpoint_id=ep_id, integration="github", event="push", signature_status="no_provider"
    )

    use_case = ListIntegrations(endpoint_repo=endpoint_repo, request_repo=request_repo)
    result = await use_case.execute_for_token("abc")

    by_int = {a.integration: a for a in result}
    assert by_int["stripe"].total == 3
    assert by_int["stripe"].event_types == {"charge.succeeded": 2, "charge.refunded": 1}
    assert by_int["stripe"].signature_status_counts == {"valid": 2, "invalid": 1}
    assert by_int["github"].total == 1
    assert by_int["github"].event_types == {"push": 1}
    assert by_int["github"].signature_status_counts == {"no_provider": 1}


async def test_list_integrations_raises_for_unknown_token(endpoint_repo, request_repo):
    """Unknown token raises EndpointNotFoundError."""
    use_case = ListIntegrations(endpoint_repo=endpoint_repo, request_repo=request_repo)
    with pytest.raises(EndpointNotFoundError):
        await use_case.execute_for_token("nonexistent")


async def test_aggregate_by_integration_empty_returns_empty_list(endpoint_repo, request_repo):
    """Endpoint with no captured requests returns empty list."""
    ep_id = uuid4()
    endpoint_repo.add(token="empty-ep", id=ep_id)

    use_case = ListIntegrations(endpoint_repo=endpoint_repo, request_repo=request_repo)
    result = await use_case.execute_for_token("empty-ep")

    assert result == []


async def test_integration_without_event_type_has_empty_event_types(endpoint_repo, request_repo):
    """Twilio-style: no event_type, but signature_status_counts still populated."""
    ep_id = uuid4()
    endpoint_repo.add(token="twilio-ep", id=ep_id)
    request_repo.add(endpoint_id=ep_id, integration="twilio", event=None, signature_status="valid")
    request_repo.add(
        endpoint_id=ep_id, integration="twilio", event=None, signature_status="missing"
    )

    use_case = ListIntegrations(endpoint_repo=endpoint_repo, request_repo=request_repo)
    result = await use_case.execute_for_token("twilio-ep")

    assert len(result) == 1
    twilio = result[0]
    assert twilio.integration == "twilio"
    assert twilio.total == 2
    assert twilio.event_types == {}
    assert twilio.signature_status_counts == {"valid": 1, "missing": 1}


async def test_sorted_by_total_descending(endpoint_repo, request_repo):
    """Results are ordered total DESC (most common integration first)."""
    ep_id = uuid4()
    endpoint_repo.add(token="sorted-ep", id=ep_id)
    # github: 1, stripe: 3
    request_repo.add(
        endpoint_id=ep_id, integration="github", event="push", signature_status="no_provider"
    )
    request_repo.add(
        endpoint_id=ep_id, integration="stripe", event="charge.succeeded", signature_status="valid"
    )
    request_repo.add(
        endpoint_id=ep_id, integration="stripe", event="charge.succeeded", signature_status="valid"
    )
    request_repo.add(
        endpoint_id=ep_id, integration="stripe", event="charge.refunded", signature_status="invalid"
    )

    use_case = ListIntegrations(endpoint_repo=endpoint_repo, request_repo=request_repo)
    result = await use_case.execute_for_token("sorted-ep")

    assert result[0].integration == "stripe"
    assert result[1].integration == "github"
