from abc import ABC, abstractmethod
from uuid import UUID


class SchemaQueue(ABC):
    """Transport-agnostic queue for schema-inference jobs. The arq impl uses
    a per-request `_job_id` so every captured request is delivered to a
    worker — no enqueue is ever dropped to dedup. Concurrency over the
    cumulative schema row is serialized at execute() time via a Postgres
    advisory lock keyed on (endpoint_id, integration, event_type), NOT by
    queue-level dedup. See UpdateInferredSchema.execute() in Step 5.
    """

    @abstractmethod
    async def enqueue(
        self,
        request_id: UUID,
        *,
        endpoint_id: UUID,
        integration: str,
        event_type: str | None,
    ) -> None: ...
