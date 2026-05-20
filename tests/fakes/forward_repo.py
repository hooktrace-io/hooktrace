from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal, get_args
from uuid import UUID

from webhook_inspector.domain.entities.forward import MAX_ATTEMPTS, Forward, ForwardStatus
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

    async def list_by_endpoint(
        self,
        endpoint_id: UUID,
        *,
        statuses: list[ForwardStatus] | None = None,
        limit: int = 50,
        before_id: UUID | None = None,
    ) -> list[Forward]:
        # Build the latest-state view across save+update by collapsing on id.
        latest: dict[UUID, Forward] = {}
        for f in self.saved:
            latest[f.id] = f
        for f in self.updated:
            latest[f.id] = f

        rows = [f for f in latest.values() if f.endpoint_id == endpoint_id]
        if statuses:
            rows = [f for f in rows if f.status in statuses]

        # Sort: created_at DESC, id DESC for a stable cursor.
        rows.sort(key=lambda f: (f.created_at, f.id), reverse=True)

        if before_id is not None:
            cursor = latest.get(before_id)
            if cursor is not None:
                rows = [f for f in rows if (f.created_at, f.id) < (cursor.created_at, cursor.id)]

        return rows[:limit]

    async def count_by_status(self, endpoint_id: UUID) -> dict[ForwardStatus, int]:
        # Same latest-state view as list_by_endpoint.
        latest: dict[UUID, Forward] = {}
        for f in self.saved:
            latest[f.id] = f
        for f in self.updated:
            latest[f.id] = f

        counts: dict[ForwardStatus, int] = dict.fromkeys(get_args(ForwardStatus), 0)
        for f in latest.values():
            if f.endpoint_id == endpoint_id:
                counts[f.status] += 1
        return counts

    async def claim_for_manual_retry(
        self,
        forward_id: UUID,
        endpoint_id: UUID,
        *,
        now: datetime,
    ) -> Forward | None:
        current = await self.find_by_id(forward_id)
        if current is None:
            return None
        if current.endpoint_id != endpoint_id:
            return None
        if current.status not in ("failed", "dead", "abandoned"):
            return None

        if current.status in ("dead", "abandoned"):
            new_attempt_count = max(0, MAX_ATTEMPTS - 1)
        else:
            new_attempt_count = current.attempt_count

        claimed = replace(
            current,
            status="pending",
            attempt_count=new_attempt_count,
            manual_retry_at=now,
            next_attempt_at=now,
            final_error=None,
            final_status_code=None,
            forward_completed_at=None,
        )
        self.updated.append(claimed)
        return claimed

    async def abandon(
        self,
        forward_id: UUID,
        endpoint_id: UUID,
        *,
        now: datetime,
    ) -> Forward | None:
        current = await self.find_by_id(forward_id)
        if current is None:
            return None
        if current.endpoint_id != endpoint_id:
            return None
        if current.status in ("succeeded", "dead", "abandoned"):
            return None

        abandoned = replace(
            current,
            status="abandoned",
            forward_completed_at=now,
            final_error="manually abandoned by owner",
        )
        self.updated.append(abandoned)
        return abandoned

    async def redrive_stuck_pending(
        self,
        endpoint_id: UUID,
        *,
        stuck_threshold_seconds: int,
        now: datetime,
    ) -> list[UUID]:
        threshold = now - timedelta(seconds=stuck_threshold_seconds)
        latest: dict[UUID, Forward] = {}
        for f in self.saved:
            latest[f.id] = f
        for f in self.updated:
            latest[f.id] = f

        stuck = [
            f
            for f in latest.values()
            if f.endpoint_id == endpoint_id and f.status == "pending" and f.created_at < threshold
        ]
        stuck.sort(key=lambda f: f.created_at)
        return [f.id for f in stuck]

    async def reclaim_stuck_in_flight(
        self,
        endpoint_id: UUID,
        *,
        stuck_threshold_seconds: int,
        now: datetime,
    ) -> list[UUID]:
        threshold = now - timedelta(seconds=stuck_threshold_seconds)
        latest: dict[UUID, Forward] = {}
        for f in self.saved:
            latest[f.id] = f
        for f in self.updated:
            latest[f.id] = f

        reclaimed_ids: list[UUID] = []
        for f in latest.values():
            if (
                f.endpoint_id == endpoint_id
                and f.status == "in_flight"
                and f.forward_started_at is not None
                and f.forward_started_at < threshold
            ):
                self.updated.append(
                    replace(f, status="failed", next_attempt_at=now),
                )
                reclaimed_ids.append(f.id)
        return reclaimed_ids
