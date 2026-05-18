from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal
from uuid import UUID

from webhook_inspector.domain.entities.forward import Forward


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
