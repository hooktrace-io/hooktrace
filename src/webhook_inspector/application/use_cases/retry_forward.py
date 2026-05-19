"""Manually retry a forward from the DLQ UI.

Atomically transitions status to 'pending' via the repo (which also resets
the attempt budget for previously-dead/abandoned rows), then re-enqueues
on the forward queue so a worker actually picks it up — flipping the
status without enqueueing leaves the row stuck (arq does not poll
Postgres).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.forward_queue import ForwardQueue
from webhook_inspector.domain.ports.forward_repository import ForwardRepository

__all__ = ["ForwardNotRetryableError", "RetryForward"]


class ForwardNotRetryableError(Exception):
    """Raised when a forward cannot be manually retried (wrong state or endpoint mismatch)."""


@dataclass
class RetryForward:
    endpoint_repo: EndpointRepository
    forward_repo: ForwardRepository
    forward_queue: ForwardQueue

    async def execute(self, *, token: str, forward_id: UUID) -> Forward:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)

        now = datetime.now(UTC)
        claimed = await self.forward_repo.claim_for_manual_retry(forward_id, endpoint.id, now=now)
        if claimed is None:
            # Cross-token mismatch OR illegal state (in_flight/succeeded). 404 for both.
            raise ForwardNotRetryableError(
                f"forward {forward_id} not retryable under endpoint {token}"
            )

        # Critical: actually re-enqueue. Flipping status to 'pending' without
        # enqueueing leaves the row stuck (arq doesn't poll Postgres).
        await self.forward_queue.enqueue(forward_id, defer_seconds=0)
        return claimed
