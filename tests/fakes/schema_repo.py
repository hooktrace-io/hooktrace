"""Canonical in-memory SchemaRepository for unit tests."""

from uuid import UUID

from webhook_inspector.domain.entities.inferred_schema import InferredSchema
from webhook_inspector.domain.ports.schema_repository import SchemaRepository


class FakeSchemaRepository(SchemaRepository):
    """In-memory SchemaRepository.

    Standard usage: pre-populate ``self.schemas`` and assert on it after
    the use-case under test runs.
    """

    def __init__(self) -> None:
        self.schemas: dict[tuple, InferredSchema] = {}

    def _key(self, endpoint_id: UUID, integration: str, event_type: str | None) -> tuple:
        return (endpoint_id, integration, event_type)

    async def find_by_key(
        self,
        *,
        endpoint_id: UUID,
        integration: str,
        event_type: str | None,
    ) -> InferredSchema | None:
        return self.schemas.get(self._key(endpoint_id, integration, event_type))

    async def upsert_with_version(self, schema: InferredSchema) -> None:
        self.schemas[self._key(schema.endpoint_id, schema.integration, schema.event_type)] = schema

    async def acquire_advisory_lock(self, key: int) -> None:
        # No-op in tests — advisory locking is a Postgres concern.
        pass
