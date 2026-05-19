from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal
from uuid import UUID

from webhook_inspector.domain.entities.forward import Forward, ForwardStatus


class ForwardRepository(ABC):
    @abstractmethod
    async def save(self, forward: Forward) -> None: ...

    @abstractmethod
    async def find_by_id(self, forward_id: UUID) -> Forward | None: ...

    @abstractmethod
    async def update(self, forward: Forward) -> None: ...

    @abstractmethod
    async def list_by_request(self, request_id: UUID) -> list[Forward]: ...

    @abstractmethod
    async def claim_for_attempt(self, forward_id: UUID, *, now: datetime) -> Forward | None:
        """Atomically transition status from {'pending', 'failed'} → 'in_flight',
        increment attempt_count, set forward_started_at and last_attempt_at to `now`.
        Returns the updated Forward, or None if the row was not in a claimable
        state (already in_flight, succeeded, or dead — duplicate fire).

        Implementation: single UPDATE ... WHERE id = ? AND status IN ('pending',
        'failed') RETURNING *. rowcount == 1 → claimed ; rowcount == 0 → skip.
        """

    @abstractmethod
    async def record_outcome(
        self,
        forward_id: UUID,
        *,
        next_status: Literal["succeeded", "failed", "dead"],
        final_status_code: int | None,
        final_error: str | None,
        next_attempt_at: datetime | None,
        now: datetime,
    ) -> None:
        """Update terminal fields after an attempt. Sets forward_completed_at=now
        for terminal statuses (succeeded, dead). Keeps it null for 'failed'
        (more attempts to come).
        """

    @abstractmethod
    async def list_by_endpoint(
        self,
        endpoint_id: UUID,
        *,
        statuses: list[ForwardStatus] | None = None,
        limit: int = 50,
        before_id: UUID | None = None,
    ) -> list[Forward]:
        """Cursor pagination matching list_requests pattern.
        Filter by one or more statuses; None means no filter.
        Order: created_at DESC, id DESC for stable cursor.
        """

    @abstractmethod
    async def count_by_status(self, endpoint_id: UUID) -> dict[ForwardStatus, int]:
        """One row per status, COUNT(*). Missing statuses → 0 in the returned dict."""

    @abstractmethod
    async def claim_for_manual_retry(
        self,
        forward_id: UUID,
        endpoint_id: UUID,
        *,
        now: datetime,
    ) -> Forward | None:
        """Atomic state transition for a manual retry triggered from the DLQ UI:
        - status must be in ('failed', 'dead', 'abandoned') to be retried
          (NOT 'in_flight' or 'succeeded')
        - status → 'pending'
        - if was 'dead' or 'abandoned': attempt_count = max(0, MAX_ATTEMPTS - 1)
          (give one more shot, don't reset to 0 → unbounded retry surface)
        - if was 'failed': attempt_count unchanged
        - manual_retry_at = now
        - next_attempt_at = now
        - final_error and final_status_code cleared

        Returns the updated Forward, or None if not claimable (in_flight,
        succeeded, or endpoint mismatch).
        """

    @abstractmethod
    async def abandon(
        self,
        forward_id: UUID,
        endpoint_id: UUID,
        *,
        now: datetime,
    ) -> Forward | None:
        """Soft-delete: transition to 'abandoned' from any non-terminal state.
        Sets forward_completed_at=now and final_error='manually abandoned by owner'.
        Returns None on endpoint mismatch or if the row is already terminal
        ('succeeded', 'dead', 'abandoned').
        """

    @abstractmethod
    async def redrive_stuck_pending(
        self,
        endpoint_id: UUID,
        *,
        stuck_threshold_seconds: int,
        now: datetime,
    ) -> list[UUID]:
        """Find forwards with status='pending' AND created_at < now - threshold.
        Returns their IDs (UNCHANGED status — the route handler then calls
        forward_queue.enqueue() for each). 5-minute threshold avoids racing
        with captures that JUST enqueued.
        """
