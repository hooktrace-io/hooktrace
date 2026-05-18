from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.integration_aggregate import IntegrationAggregate
from webhook_inspector.domain.ports.request_repository import RequestRepository
from webhook_inspector.infrastructure.database.models import RequestTable


class PostgresRequestRepository(RequestRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, request: CapturedRequest) -> None:
        # Every field on CapturedRequest must appear below. test_postgres_save_fields
        # guards the omission, but silent drops have shipped twice (PR1.4, PR2.3) —
        # if you add a field, mirror it here AND in _to_entity.
        row = RequestTable(
            id=request.id,
            endpoint_id=request.endpoint_id,
            method=request.method,
            path=request.path,
            query_string=request.query_string,
            headers=request.headers,
            body_preview=request.body_preview,
            body_size=request.body_size,
            blob_key=request.blob_key,
            source_ip=request.source_ip,
            received_at=request.received_at,
            signature_status=request.signature_status,
            detected_integration=request.detected_integration,
            detected_event_type=request.detected_event_type,
        )
        self._session.add(row)
        await self._session.flush()
        # Emit NOTIFY in the SAME transaction. Commit will bundle INSERT + NOTIFY
        # atomically, so listeners cannot see the notification before the row is
        # visible.
        await self._session.execute(
            text("SELECT pg_notify('new_request', :payload);"),
            {"payload": f"{request.endpoint_id}:{request.id}"},
        )

    async def find_by_id(self, request_id: UUID) -> CapturedRequest | None:
        stmt = select(RequestTable).where(RequestTable.id == request_id)  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list_by_endpoint(
        self,
        endpoint_id: UUID,
        limit: int = 50,
        before_id: UUID | None = None,
        q: str | None = None,
    ) -> list[CapturedRequest]:
        stmt = (
            select(RequestTable)
            .where(RequestTable.endpoint_id == endpoint_id)  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
            .order_by(RequestTable.received_at.desc(), RequestTable.id.desc())  # type: ignore[attr-defined]  # SQLAlchemy column descriptors have .desc() at runtime
            .limit(limit)
        )

        if q:
            stmt = stmt.where(
                text("search_vector @@ plainto_tsquery('simple', :q)").bindparams(q=q)
            )

        if before_id is not None:
            cursor_row = (
                await self._session.execute(
                    select(RequestTable.received_at, RequestTable.id).where(  # type: ignore[call-overload]  # SQLAlchemy/mypy strict incompat
                        RequestTable.id == before_id
                    )
                )
            ).one_or_none()
            if cursor_row is not None:
                cursor_ts, cursor_id = cursor_row
                stmt = stmt.where(
                    (RequestTable.received_at < cursor_ts)
                    | ((RequestTable.received_at == cursor_ts) & (RequestTable.id < cursor_id))
                )

        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(r) for r in rows]

    async def stream_for_export(
        self,
        endpoint_id: UUID,
        max_count: int,
    ) -> AsyncIterator[CapturedRequest]:
        stmt = (
            select(RequestTable)
            .where(RequestTable.endpoint_id == endpoint_id)  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
            .order_by(RequestTable.received_at.desc(), RequestTable.id.desc())  # type: ignore[attr-defined]  # SQLAlchemy column descriptors have .desc() at runtime
            .limit(max_count)
            .execution_options(yield_per=100)
        )
        result = await self._session.stream(stmt)
        async for row in result.scalars():
            yield _to_entity(row)

    async def count_by_endpoint(self, endpoint_id: UUID) -> int:
        stmt = select(func.count(RequestTable.id)).where(  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
            RequestTable.endpoint_id == endpoint_id  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def aggregate_by_integration(self, endpoint_id: UUID) -> list[IntegrationAggregate]:  # noqa: ARG002
        # Stub — real 3-CTE implementation added in Task 2.
        return []


def _to_entity(row: RequestTable) -> CapturedRequest:
    return CapturedRequest(
        id=row.id,
        endpoint_id=row.endpoint_id,
        method=row.method,
        path=row.path,
        query_string=row.query_string,
        headers=row.headers,
        body_preview=row.body_preview,
        body_size=row.body_size,
        blob_key=row.blob_key,
        source_ip=row.source_ip,
        received_at=row.received_at,
        signature_status=row.signature_status,
        detected_integration=row.detected_integration,
        detected_event_type=row.detected_event_type,
    )
