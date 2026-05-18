"""Unit tests for the integrations API route and Pydantic schema.

No database required — uses FastAPI dependency overrides with an in-memory
ListIntegrations use case.
"""

from fastapi.testclient import TestClient

from webhook_inspector.domain.entities.integration_aggregate import IntegrationAggregate
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.web.app.deps import get_list_integrations
from webhook_inspector.web.app.routes import IntegrationAggregateResponse

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _StubListIntegrations:
    """Minimal stub that replaces the real ListIntegrations use case."""

    def __init__(self, result: list[IntegrationAggregate] | None = None, raise_404: bool = False):
        self._result = result or []
        self._raise_404 = raise_404

    async def execute_for_token(self, token: str) -> list[IntegrationAggregate]:
        if self._raise_404:
            raise EndpointNotFoundError(token)
        return self._result


def _make_client(stub: _StubListIntegrations) -> TestClient:
    # Import app lazily to avoid side effects at module level
    from webhook_inspector.web.app.main import app

    app.dependency_overrides[get_list_integrations] = lambda: stub
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Schema unit test
# ---------------------------------------------------------------------------


def test_integration_aggregate_response_schema():
    """IntegrationAggregateResponse serialises all fields correctly."""
    resp = IntegrationAggregateResponse(
        integration="stripe",
        total=5,
        event_types={"charge.succeeded": 3, "charge.refunded": 2},
        signature_status_counts={"valid": 3, "invalid": 1, "missing": 1},
    )
    payload = resp.model_dump()
    assert payload["integration"] == "stripe"
    assert payload["total"] == 5
    assert payload["event_types"]["charge.succeeded"] == 3
    assert payload["signature_status_counts"]["valid"] == 3


# ---------------------------------------------------------------------------
# Route tests via FastAPI dependency override
# ---------------------------------------------------------------------------


def test_list_integrations_route_returns_200_with_data():
    """GET /api/endpoints/{token}/integrations returns aggregated data."""
    from webhook_inspector.web.app.main import app

    aggregates = [
        IntegrationAggregate(
            integration="stripe",
            total=3,
            event_types={"charge.succeeded": 2, "charge.refunded": 1},
            signature_status_counts={"valid": 2, "invalid": 1},
        ),
        IntegrationAggregate(
            integration="github",
            total=1,
            event_types={"push": 1},
            signature_status_counts={"no_provider": 1},
        ),
    ]
    stub = _StubListIntegrations(result=aggregates)
    app.dependency_overrides[get_list_integrations] = lambda: stub

    client = TestClient(app)
    resp = client.get("/api/endpoints/tok123/integrations")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    by_int = {item["integration"]: item for item in data}
    assert by_int["stripe"]["total"] == 3
    assert by_int["stripe"]["event_types"] == {"charge.succeeded": 2, "charge.refunded": 1}
    assert by_int["stripe"]["signature_status_counts"] == {"valid": 2, "invalid": 1}
    assert by_int["github"]["total"] == 1


def test_list_integrations_route_returns_404_for_unknown_token():
    """GET /api/endpoints/{token}/integrations returns 404 for unknown token."""
    from webhook_inspector.web.app.main import app

    stub = _StubListIntegrations(raise_404=True)
    app.dependency_overrides[get_list_integrations] = lambda: stub

    client = TestClient(app)
    resp = client.get("/api/endpoints/nosuchtok/integrations")
    app.dependency_overrides.clear()

    assert resp.status_code == 404
    assert "endpoint not found" in resp.json()["detail"]


def test_list_integrations_route_returns_empty_list():
    """GET /api/endpoints/{token}/integrations returns [] when no integrations detected."""
    from webhook_inspector.web.app.main import app

    stub = _StubListIntegrations(result=[])
    app.dependency_overrides[get_list_integrations] = lambda: stub

    client = TestClient(app)
    resp = client.get("/api/endpoints/empty-tok/integrations")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []
