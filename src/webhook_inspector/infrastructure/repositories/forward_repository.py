from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.ports.forward_repository import ForwardRepository
from webhook_inspector.infrastructure.database.models import ForwardTable


class PostgresForwardRepository(ForwardRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, forward: Forward) -> None:
        # Every field on Forward must appear below AND in _to_entity. Silent
        # drops shipped twice in earlier PRs (PR1.4 signature_status,
        # PR2.3 detected_integration) before the regression test caught them.
        # If you add a field, mirror it here AND in _to_entity.
        row = ForwardTable(
            id=forward.id,
            request_id=forward.request_id,
            endpoint_id=forward.endpoint_id,
            target_url=forward.target_url,
            status=forward.status,
            attempt_count=forward.attempt_count,
            last_attempt_at=forward.last_attempt_at,
            next_attempt_at=forward.next_attempt_at,
            final_status_code=forward.final_status_code,
            final_error=forward.final_error,
            forward_started_at=forward.forward_started_at,
            forward_completed_at=forward.forward_completed_at,
            created_at=forward.created_at,
            manual_retry_at=forward.manual_retry_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def find_by_id(self, forward_id: UUID) -> Forward | None:
        stmt = select(ForwardTable).where(ForwardTable.id == forward_id)  # type: ignore[arg-type]
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def update(self, forward: Forward) -> None:
        stmt = (
            update(ForwardTable)
            .where(ForwardTable.id == forward.id)  # type: ignore[arg-type]
            .values(
                status=forward.status,
                attempt_count=forward.attempt_count,
                last_attempt_at=forward.last_attempt_at,
                next_attempt_at=forward.next_attempt_at,
                final_status_code=forward.final_status_code,
                final_error=forward.final_error,
                forward_started_at=forward.forward_started_at,
                forward_completed_at=forward.forward_completed_at,
                manual_retry_at=forward.manual_retry_at,
            )
        )
        await self._session.execute(stmt)

    async def list_by_request(self, request_id: UUID) -> list[Forward]:
        stmt = (
            select(ForwardTable)
            .where(ForwardTable.request_id == request_id)  # type: ignore[arg-type]
            .order_by(ForwardTable.created_at.asc())  # type: ignore[attr-defined]
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(r) for r in rows]

    async def claim_for_attempt(self, forward_id: UUID, *, now: datetime) -> Forward | None:
        stmt = (
            update(ForwardTable)
            .where(
                ForwardTable.id == forward_id,  # type: ignore[arg-type]
                ForwardTable.status.in_(("pending", "failed")),  # type: ignore[attr-defined]
            )
            .values(
                status="in_flight",
                attempt_count=ForwardTable.attempt_count + 1,
                last_attempt_at=now,
                forward_started_at=now,
            )
            .returning(ForwardTable)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_entity(row) if row is not None else None

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
        completed_at = now if next_status in ("succeeded", "dead") else None
        stmt = (
            update(ForwardTable)
            .where(ForwardTable.id == forward_id)  # type: ignore[arg-type]
            .values(
                status=next_status,
                final_status_code=final_status_code,
                final_error=final_error,
                next_attempt_at=next_attempt_at,
                forward_completed_at=completed_at,
            )
        )
        await self._session.execute(stmt)


def _to_entity(row: ForwardTable) -> Forward:
    return Forward(
        id=row.id,
        request_id=row.request_id,
        endpoint_id=row.endpoint_id,
        target_url=row.target_url,
        status=row.status,  # type: ignore[arg-type]
        attempt_count=row.attempt_count,
        last_attempt_at=row.last_attempt_at,
        next_attempt_at=row.next_attempt_at,
        final_status_code=row.final_status_code,
        final_error=row.final_error,
        forward_started_at=row.forward_started_at,
        forward_completed_at=row.forward_completed_at,
        created_at=row.created_at,
        manual_retry_at=row.manual_retry_at,
    )
