"""Canonical in-memory EndpointRepository for unit tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository


class FakeEndpointRepo(EndpointRepository):
    """In-memory EndpointRepository.

    Construction patterns:
    - ``FakeEndpointRepo()`` — empty store.
    - ``FakeEndpointRepo(seed=ep)`` — pre-populated with one endpoint.
    - ``repo.add(token="abc", id=some_uuid)`` — factory helper for integrations
      tests that build their own fixture sets.
    """

    def __init__(self, seed: Endpoint | None = None):
        self.saved: list[Endpoint] = [seed] if seed else []
        self.increments: list[UUID] = []
        self.updated: list[Endpoint] = []

    def add(self, *, token: str, id: str | UUID) -> Endpoint:
        """Create an Endpoint with sensible defaults and store it."""
        ep_id = UUID(str(id)) if isinstance(id, str) else id
        ep = Endpoint(
            id=ep_id,
            token=token,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            request_count=0,
        )
        self.saved.append(ep)
        return ep

    async def save(self, endpoint: Endpoint) -> None:
        self.saved.append(endpoint)

    async def find_by_token(self, token: str) -> Endpoint | None:
        return next((e for e in self.saved if e.token == token), None)

    async def find_by_id(self, endpoint_id: UUID) -> Endpoint | None:
        return next((e for e in self.saved if e.id == endpoint_id), None)

    async def update(self, endpoint: Endpoint) -> None:
        self.updated.append(endpoint)

    async def increment_request_count(self, endpoint_id: UUID) -> None:
        self.increments.append(endpoint_id)

    async def delete_expired(self) -> int:
        return 0

    async def count_active(self) -> int:
        return len([e for e in self.saved if e is not None and not e.is_expired()])
