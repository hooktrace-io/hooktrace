"""No-op SchemaQueue used when Redis is not configured (e.g. local dev)."""

import logging
from uuid import UUID

from webhook_inspector.domain.ports.schema_queue import SchemaQueue

logger = logging.getLogger(__name__)


class NullSchemaQueue(SchemaQueue):
    """Schema queue that discards all enqueue calls silently.
    Used when REDIS_URL is not set — local dev without Redis.
    Schema drift will not be computed; all other capture functionality works.
    """

    async def enqueue(
        self,
        request_id: UUID,
        *,
        endpoint_id: UUID,  # noqa: ARG002
        integration: str,  # noqa: ARG002
        event_type: str | None,  # noqa: ARG002
    ) -> None:
        logger.debug(
            "schema_enqueue_skipped_no_redis",
            extra={"request_id": str(request_id)},
        )
