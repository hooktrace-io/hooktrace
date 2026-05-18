from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.integration_aggregate import IntegrationAggregate


class RequestRepository(ABC):
    @abstractmethod
    async def save(self, request: CapturedRequest) -> None: ...

    @abstractmethod
    async def find_by_id(self, request_id: UUID) -> CapturedRequest | None: ...

    @abstractmethod
    async def list_by_endpoint(
        self,
        endpoint_id: UUID,
        limit: int = 50,
        before_id: UUID | None = None,
        q: str | None = None,
    ) -> list[CapturedRequest]: ...

    @abstractmethod
    def stream_for_export(
        self,
        endpoint_id: UUID,
        max_count: int,
    ) -> AsyncIterator[CapturedRequest]:
        """Yield captured requests ordered by received_at DESC, capped at max_count."""
        ...

    @abstractmethod
    async def count_by_endpoint(self, endpoint_id: UUID) -> int:
        """Return total number of captured requests for the endpoint."""
        ...

    @abstractmethod
    async def aggregate_by_integration(self, endpoint_id: UUID) -> list[IntegrationAggregate]:
        """Return per-integration request aggregates for the endpoint."""
        ...

    @abstractmethod
    async def update_schema_drift(self, request_id: UUID, drift: dict[str, Any] | None) -> None: ...
