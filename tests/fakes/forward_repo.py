from dataclasses import replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.ports.forward_repository import ForwardRepository


class FakeForwardRepository(ForwardRepository):
    def __init__(self) -> None:
        self.saved: list[Forward] = []
        self.updated: list[Forward] = []

    async def save(self, forward: Forward) -> None:
        self.saved.append(forward)

    async def find_by_id(self, forward_id: UUID) -> Forward | None:
        # Return the most-recent state (search updates first, then saves)
        for f in reversed(self.updated):
            if f.id == forward_id:
                return f
        for f in self.saved:
            if f.id == forward_id:
                return f
        return None

    async def update(self, forward: Forward) -> None:
        self.updated.append(forward)

    async def list_by_request(self, request_id: UUID) -> list[Forward]:
        return [f for f in self.saved if f.request_id == request_id]

    async def claim_for_attempt(self, forward_id: UUID, *, now: datetime) -> Forward | None:
        current = await self.find_by_id(forward_id)
        if current is None or current.status not in ("pending", "failed"):
            return None
        claimed = replace(
            current,
            status="in_flight",
            attempt_count=current.attempt_count + 1,
            last_attempt_at=now,
            forward_started_at=now,
        )
        self.updated.append(claimed)
        return claimed

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
        current = await self.find_by_id(forward_id)
        if current is None:
            return
        completed_at = now if next_status in ("succeeded", "dead") else None
        self.updated.append(
            replace(
                current,
                status=next_status,
                final_status_code=final_status_code,
                final_error=final_error,
                next_attempt_at=next_attempt_at,
                forward_completed_at=completed_at,
            )
        )
