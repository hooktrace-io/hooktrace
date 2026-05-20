"""Operator-initiated redrive of pending-but-stuck forwards.

When Redis flaps or the worker pool wedges, freshly-captured forwards can
sit in `status='pending'` forever — arq lost the in-memory job and never
re-polls Postgres. This use case finds rows older than the threshold and
re-enqueues each one.

Return value is the count of *eligible* rows, NOT successfully enqueued
rows: per-row enqueue failures are logged and swallowed so partial
progress is acceptable. The row stays in 'pending', so the next redrive
will pick it up again.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.forward_queue import ForwardQueue
from webhook_inspector.domain.ports.forward_repository import ForwardRepository

__all__ = [
    "STUCK_IN_FLIGHT_THRESHOLD_SECONDS",
    "STUCK_PENDING_THRESHOLD_SECONDS",
    "RedrivePendingForwards",
]

logger = logging.getLogger(__name__)

STUCK_PENDING_THRESHOLD_SECONDS = 5 * 60  # 5 minutes
# `in_flight` is only legitimate during a single attempt (HTTP timeout = 10s).
# Anything stuck > 5 min in_flight is a worker crash between claim-commit and
# record_outcome — reclaim it so the natural retry path picks it back up.
STUCK_IN_FLIGHT_THRESHOLD_SECONDS = 5 * 60


@dataclass
class RedrivePendingForwards:
    endpoint_repo: EndpointRepository
    forward_repo: ForwardRepository
    forward_queue: ForwardQueue

    async def execute(self, *, token: str) -> int:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)

        now = datetime.now(UTC)
        # First: reclaim any `in_flight` rows stuck past the threshold (worker
        # crashed mid-attempt). The repo atomically flips them to 'failed' so
        # the next claim_for_attempt picks them up via the failed branch.
        reclaimed_ids = await self.forward_repo.reclaim_stuck_in_flight(
            endpoint.id,
            stuck_threshold_seconds=STUCK_IN_FLIGHT_THRESHOLD_SECONDS,
            now=now,
        )
        # Then: redrive stuck pendings (capture committed but enqueue lost).
        pending_ids = await self.forward_repo.redrive_stuck_pending(
            endpoint.id,
            stuck_threshold_seconds=STUCK_PENDING_THRESHOLD_SECONDS,
            now=now,
        )
        for fid in [*reclaimed_ids, *pending_ids]:
            try:
                await self.forward_queue.enqueue(fid, defer_seconds=0)
            except Exception:
                # Redis still down? Log + count; the row stays pending/failed
                # so the next redrive will catch it. NEVER re-raise — partial
                # progress is acceptable. logger.exception keeps the
                # traceback in the structured log and satisfies BLE001.
                logger.exception("redrive_enqueue_failed", extra={"forward_id": str(fid)})
        return len(reclaimed_ids) + len(pending_ids)
