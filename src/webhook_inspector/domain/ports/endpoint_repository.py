from abc import ABC, abstractmethod
from uuid import UUID

from webhook_inspector.domain.entities.endpoint import Endpoint


class EndpointRepository(ABC):
    @abstractmethod
    async def save(self, endpoint: Endpoint) -> None: ...

    @abstractmethod
    async def update(self, endpoint: Endpoint) -> None:
        """UPDATE the row matching endpoint.id with the entity's current state.
        Raises EndpointNotFoundError if the row doesn't exist."""

    @abstractmethod
    async def find_by_token(self, token: str) -> Endpoint | None: ...

    @abstractmethod
    async def find_by_id(self, endpoint_id: UUID) -> Endpoint | None: ...

    @abstractmethod
    async def increment_request_count(self, endpoint_id: UUID) -> None: ...

    @abstractmethod
    async def delete_expired(self) -> int:
        """Delete expired endpoints. Returns count of deleted rows."""

    @abstractmethod
    async def count_active(self) -> int:
        """Count endpoints where expires_at > NOW()."""
