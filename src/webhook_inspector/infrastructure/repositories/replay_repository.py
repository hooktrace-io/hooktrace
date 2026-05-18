from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.domain.entities.replay import Replay
from webhook_inspector.domain.ports.replay_repository import ReplayRepository
from webhook_inspector.infrastructure.database.models import ReplayTable


class PostgresReplayRepository(ReplayRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, replay: Replay) -> None:
        row = ReplayTable(
            id=replay.id,
            request_id=replay.request_id,
            target_url=replay.target_url,
            status_code=replay.status_code,
            response_body_preview=replay.response_body_preview,
            response_headers=replay.response_headers,
            error=replay.error,
            duration_ms=replay.duration_ms,
            attempted_at=replay.attempted_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def list_by_request(self, request_id: UUID) -> list[Replay]:
        stmt = (
            select(ReplayTable)
            .where(ReplayTable.request_id == request_id)  # type: ignore[arg-type]
            .order_by(ReplayTable.attempted_at.desc())  # type: ignore[attr-defined]
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(r) for r in rows]


def _to_entity(row: ReplayTable) -> Replay:
    return Replay(
        id=row.id,
        request_id=row.request_id,
        target_url=row.target_url,
        status_code=row.status_code,
        response_body_preview=row.response_body_preview,
        response_headers=row.response_headers,
        error=row.error,
        duration_ms=row.duration_ms,
        attempted_at=row.attempted_at,
    )
