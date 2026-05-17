from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import SlugAlreadyTakenError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.infrastructure.database.models import EndpointTable


class PostgresEndpointRepository(EndpointRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, endpoint: Endpoint) -> None:
        row = EndpointTable(
            id=endpoint.id,
            token=endpoint.token,
            created_at=endpoint.created_at,
            expires_at=endpoint.expires_at,
            request_count=endpoint.request_count,
            response_status_code=endpoint.response_status_code,
            response_body=endpoint.response_body,
            response_headers=endpoint.response_headers,
            response_delay_ms=endpoint.response_delay_ms,
            signature_provider=endpoint.signature_provider,
            signature_secret_encrypted=endpoint.signature_secret_encrypted,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as e:
            raise SlugAlreadyTakenError(
                f"endpoint with token '{endpoint.token}' already exists"
            ) from e

    async def update(self, endpoint: Endpoint) -> None:
        stmt = (
            update(EndpointTable)
            .where(EndpointTable.id == endpoint.id)  # type: ignore[arg-type]
            .values(
                signature_provider=endpoint.signature_provider,
                signature_secret_encrypted=endpoint.signature_secret_encrypted,
                response_status_code=endpoint.response_status_code,
                response_body=endpoint.response_body,
                response_headers=endpoint.response_headers,
                response_delay_ms=endpoint.response_delay_ms,
            )
        )
        result = await self._session.execute(stmt)
        if (result.rowcount or 0) == 0:  # type: ignore[attr-defined]
            from webhook_inspector.domain.exceptions import EndpointNotFoundError

            raise EndpointNotFoundError(f"endpoint id={endpoint.id} not found")

    async def find_by_token(self, token: str) -> Endpoint | None:
        stmt = select(EndpointTable).where(EndpointTable.token == token)  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def find_by_id(self, endpoint_id: UUID) -> Endpoint | None:
        stmt = select(EndpointTable).where(EndpointTable.id == endpoint_id)  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def increment_request_count(self, endpoint_id: UUID) -> None:
        stmt = (
            update(EndpointTable)
            .where(EndpointTable.id == endpoint_id)  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
            .values(request_count=EndpointTable.request_count + 1)
        )
        await self._session.execute(stmt)

    async def delete_expired(self) -> int:
        stmt = delete(EndpointTable).where(EndpointTable.expires_at < datetime.now(UTC))  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
        result = await self._session.execute(stmt)
        return result.rowcount or 0  # type: ignore[attr-defined]  # CursorResult has rowcount at runtime

    async def count_active(self) -> int:
        stmt = select(func.count(EndpointTable.id)).where(  # type: ignore[arg-type]
            EndpointTable.expires_at > datetime.now(UTC)  # type: ignore[arg-type]
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)


def _to_entity(row: EndpointTable) -> Endpoint:
    return Endpoint(
        id=row.id,
        token=row.token,
        created_at=row.created_at,
        expires_at=row.expires_at,
        request_count=row.request_count,
        response_status_code=row.response_status_code,
        response_body=row.response_body,
        response_headers=row.response_headers,
        response_delay_ms=row.response_delay_ms,
        signature_provider=row.signature_provider,
        signature_secret_encrypted=row.signature_secret_encrypted,
    )
