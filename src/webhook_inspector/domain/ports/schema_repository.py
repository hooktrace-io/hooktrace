from abc import ABC, abstractmethod
from uuid import UUID

from webhook_inspector.domain.entities.inferred_schema import InferredSchema


class SchemaRepository(ABC):
    @abstractmethod
    async def find_by_key(
        self,
        *,
        endpoint_id: UUID,
        integration: str,
        event_type: str | None,
    ) -> InferredSchema | None: ...

    @abstractmethod
    async def upsert_with_version(self, schema: InferredSchema) -> None: ...
