from datetime import datetime, timedelta
from typing import Literal, get_args
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.domain.entities.forward import MAX_ATTEMPTS, Forward, ForwardStatus
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

    async def list_by_endpoint(
        self,
        endpoint_id: UUID,
        *,
        statuses: list[ForwardStatus] | None = None,
        limit: int = 50,
        before_id: UUID | None = None,
    ) -> list[Forward]:
        stmt = (
            select(ForwardTable)
            .where(ForwardTable.endpoint_id == endpoint_id)  # type: ignore[arg-type]
            .order_by(ForwardTable.created_at.desc(), ForwardTable.id.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )

        if statuses:
            stmt = stmt.where(ForwardTable.status.in_(statuses))  # type: ignore[attr-defined]

        if before_id is not None:
            cursor_row = (
                await self._session.execute(
                    select(ForwardTable.created_at, ForwardTable.id).where(  # type: ignore[call-overload]
                        ForwardTable.id == before_id
                    )
                )
            ).one_or_none()
            if cursor_row is not None:
                cursor_ts, cursor_id = cursor_row
                stmt = stmt.where(
                    (ForwardTable.created_at < cursor_ts)
                    | ((ForwardTable.created_at == cursor_ts) & (ForwardTable.id < cursor_id))
                )

        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(r) for r in rows]

    async def count_by_status(self, endpoint_id: UUID) -> dict[ForwardStatus, int]:
        stmt = (
            select(ForwardTable.status, func.count(ForwardTable.id))  # type: ignore[call-overload,arg-type]
            .where(ForwardTable.endpoint_id == endpoint_id)
            .group_by(ForwardTable.status)
        )
        rows = (await self._session.execute(stmt)).all()
        # Pre-populate every status with 0 so callers always get all 6 keys.
        result: dict[ForwardStatus, int] = dict.fromkeys(get_args(ForwardStatus), 0)
        for status, count in rows:
            # status comes back as the raw TEXT value from Postgres ; the CHECK
            # constraint guarantees it's a member of ForwardStatus.
            result[status] = int(count)
        return result

    async def claim_for_manual_retry(
        self,
        forward_id: UUID,
        endpoint_id: UUID,
        *,
        now: datetime,
    ) -> Forward | None:
        # attempt_count: if status was 'dead' or 'abandoned', drop back to
        # MAX_ATTEMPTS - 1 (give one more shot, not unbounded retries).
        # If was 'failed', leave attempt_count alone (the normal retry path
        # would have incremented it for us).
        new_attempt_count = case(
            (
                ForwardTable.status.in_(("dead", "abandoned")),  # type: ignore[attr-defined]
                max(0, MAX_ATTEMPTS - 1),
            ),
            else_=ForwardTable.attempt_count,
        )
        stmt = (
            update(ForwardTable)
            .where(
                ForwardTable.id == forward_id,  # type: ignore[arg-type]
                ForwardTable.endpoint_id == endpoint_id,  # type: ignore[arg-type]
                ForwardTable.status.in_(("failed", "dead", "abandoned")),  # type: ignore[attr-defined]
            )
            .values(
                status="pending",
                attempt_count=new_attempt_count,
                manual_retry_at=now,
                next_attempt_at=now,
                final_error=None,
                final_status_code=None,
                forward_completed_at=None,
            )
            .returning(ForwardTable)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_entity(row) if row is not None else None

    async def abandon(
        self,
        forward_id: UUID,
        endpoint_id: UUID,
        *,
        now: datetime,
    ) -> Forward | None:
        stmt = (
            update(ForwardTable)
            .where(
                ForwardTable.id == forward_id,  # type: ignore[arg-type]
                ForwardTable.endpoint_id == endpoint_id,  # type: ignore[arg-type]
                ForwardTable.status.notin_(("succeeded", "dead", "abandoned")),  # type: ignore[attr-defined]
            )
            .values(
                status="abandoned",
                forward_completed_at=now,
                final_error="manually abandoned by owner",
            )
            .returning(ForwardTable)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_entity(row) if row is not None else None

    async def redrive_stuck_pending(
        self,
        endpoint_id: UUID,
        *,
        stuck_threshold_seconds: int,
        now: datetime,
    ) -> list[UUID]:
        threshold = now - timedelta(seconds=stuck_threshold_seconds)
        stmt = (
            select(ForwardTable.id)  # type: ignore[call-overload]
            .where(
                ForwardTable.endpoint_id == endpoint_id,
                ForwardTable.status == "pending",
                ForwardTable.created_at < threshold,
            )
            .order_by(ForwardTable.created_at.asc())  # type: ignore[attr-defined]
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    async def reclaim_stuck_in_flight(
        self,
        endpoint_id: UUID,
        *,
        stuck_threshold_seconds: int,
        now: datetime,
    ) -> list[UUID]:
        threshold = now - timedelta(seconds=stuck_threshold_seconds)
        stmt = (
            update(ForwardTable)
            .where(
                ForwardTable.endpoint_id == endpoint_id,  # type: ignore[arg-type]
                ForwardTable.status == "in_flight",  # type: ignore[arg-type]
                ForwardTable.forward_started_at < threshold,  # type: ignore[arg-type,operator]
            )
            .values(status="failed", next_attempt_at=now)
            .returning(ForwardTable.id)  # type: ignore[call-overload]
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def list_overdue_failed(
        self,
        endpoint_id: UUID,
        *,
        now: datetime,
    ) -> list[UUID]:
        stmt = (
            select(ForwardTable.id)  # type: ignore[call-overload]
            .where(
                ForwardTable.endpoint_id == endpoint_id,
                ForwardTable.status == "failed",
                ForwardTable.next_attempt_at.isnot(None),  # type: ignore[union-attr]
                ForwardTable.next_attempt_at <= now,  # type: ignore[operator]
            )
            .order_by(ForwardTable.next_attempt_at.asc())  # type: ignore[union-attr]
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)


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
