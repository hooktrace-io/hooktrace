"""Canonical in-memory RequestRepository for unit tests."""

from uuid import UUID

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.integration_aggregate import IntegrationAggregate
from webhook_inspector.domain.ports.request_repository import RequestRepository


class FakeRequestRepo(RequestRepository):
    """In-memory RequestRepository.

    Standard usage: call ``save(CapturedRequest)`` and assert on ``self.saved``.

    For integrations tests that need to drive aggregation behaviour:
    use ``add(endpoint_id=..., integration=..., event=..., signature_status=...)``
    instead of building full CapturedRequest objects.  ``aggregate_by_integration``
    does the real in-memory cross-tab so tests that exercise ListIntegrations
    work without subclassing.
    """

    def __init__(self, items: list[CapturedRequest] | None = None):
        self.saved: list[CapturedRequest] = list(items) if items else []
        self._dict_items: list[dict] = []

    def add(
        self,
        *,
        endpoint_id: str | UUID,
        integration: str,
        event: str | None = None,
        signature_status: str = "no_provider",
    ) -> None:
        """Add a lightweight dict entry used by ``aggregate_by_integration``."""
        self._dict_items.append(
            {
                "endpoint_id": UUID(str(endpoint_id))
                if isinstance(endpoint_id, str)
                else endpoint_id,
                "integration": integration,
                "event": event,
                "signature_status": signature_status,
            }
        )

    async def save(self, request: CapturedRequest) -> None:
        self.saved.append(request)

    async def find_by_id(self, request_id: UUID) -> CapturedRequest | None:
        return next((r for r in self.saved if r.id == request_id), None)

    async def list_by_endpoint(
        self,
        endpoint_id: UUID,
        limit: int = 50,
        before_id: UUID | None = None,
        q: str | None = None,
    ) -> list[CapturedRequest]:
        return [r for r in self.saved if r.endpoint_id == endpoint_id][:limit]

    async def stream_for_export(self, endpoint_id: UUID, max_count: int):
        for r in [x for x in self.saved if x.endpoint_id == endpoint_id][:max_count]:
            yield r

    async def count_by_endpoint(self, endpoint_id: UUID) -> int:
        return len([r for r in self.saved if r.endpoint_id == endpoint_id])

    async def aggregate_by_integration(self, endpoint_id: UUID) -> list[IntegrationAggregate]:
        """In-memory aggregation matching the PostgreSQL 3-CTE behaviour."""
        relevant = [
            item
            for item in self._dict_items
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
