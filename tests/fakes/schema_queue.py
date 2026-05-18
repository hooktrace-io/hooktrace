"""Canonical in-memory SchemaQueue for unit tests."""

from uuid import UUID

from webhook_inspector.domain.ports.schema_queue import SchemaQueue


class FakeSchemaQueue(SchemaQueue):
    """In-memory SchemaQueue. Records enqueue calls for assertion."""

    def __init__(self, *, fail: bool = False) -> None:
        self.enqueued: list[dict] = []
        self.fail = fail

    async def enqueue(
        self,
        request_id: UUID,
        *,
        endpoint_id: UUID,
        integration: str,
        event_type: str | None,
    ) -> None:
        if self.fail:
            raise RuntimeError("redis down")
        self.enqueued.append(
            {
                "request_id": request_id,
                "endpoint_id": endpoint_id,
                "integration": integration,
                "event_type": event_type,
            }
        )
