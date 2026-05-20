"""Unit tests for ListIntegrations use case.

Uses in-memory fakes — Docker / testcontainers NOT required.
"""

from uuid import uuid4

import pytest

from tests.fakes import FakeEndpointRepo, FakeRequestRepo
from webhook_inspector.application.use_cases.list_integrations import ListIntegrations
from webhook_inspector.domain.exceptions import EndpointNotFoundError

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
    result = await use_case.execute(token="abc")

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
        await use_case.execute(token="nonexistent")


async def test_aggregate_by_integration_empty_returns_empty_list(endpoint_repo, request_repo):
    """Endpoint with no captured requests returns empty list."""
    ep_id = uuid4()
    endpoint_repo.add(token="empty-ep", id=ep_id)

    use_case = ListIntegrations(endpoint_repo=endpoint_repo, request_repo=request_repo)
    result = await use_case.execute(token="empty-ep")

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
    result = await use_case.execute(token="twilio-ep")

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
    result = await use_case.execute(token="sorted-ep")

    assert result[0].integration == "stripe"
    assert result[1].integration == "github"
