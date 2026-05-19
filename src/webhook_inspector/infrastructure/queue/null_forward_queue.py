"""Dev fallback for ForwardQueue: when REDIS_URL is unset, no-op enqueue.

The forward row is still persisted (status='pending') so a future ops redrive
can pick it up once Redis is provisioned. For local dev without Redis, this
means forward never fires — acceptable.
"""

import logging
from uuid import UUID

from webhook_inspector.domain.ports.forward_queue import ForwardQueue

logger = logging.getLogger(__name__)


class NullForwardQueue(ForwardQueue):
    async def enqueue(self, forward_id: UUID, *, defer_seconds: int = 0) -> None:
        logger.debug(
            "forward_enqueue_skipped_no_redis",
            extra={"forward_id": str(forward_id), "defer_seconds": defer_seconds},
        )

    async def aclose(self) -> None:
        pass
