import json
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.domain.entities.inferred_schema import InferredSchema
from webhook_inspector.domain.ports.schema_repository import SchemaRepository
from webhook_inspector.infrastructure.database.models import InferredSchemaTable


class PostgresSchemaRepository(SchemaRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_key(
        self,
        *,
        endpoint_id: UUID,
        integration: str,
        event_type: str | None,
    ) -> InferredSchema | None:
        # COALESCE matches the partial unique index in migration 0006.
        # mypy cannot infer SQLAlchemy column expression return types, so we
        # collect clauses via a list and unpack.
        clauses: list[ColumnElement[Any]] = [
            InferredSchemaTable.endpoint_id == endpoint_id,  # type: ignore[list-item]
            InferredSchemaTable.integration == integration,  # type: ignore[list-item]
        ]
        if event_type is not None:
            clauses.append(InferredSchemaTable.event_type == event_type)  # type: ignore[arg-type]
        else:
            clauses.append(text("event_type IS NULL"))  # type: ignore[arg-type]
        stmt = select(InferredSchemaTable).where(*clauses)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def upsert_with_version(self, schema: InferredSchema) -> None:
        # Use ON CONFLICT to atomically upsert under the unique index. The
        # serialization for concurrent workers comes from pg_advisory_xact_lock
        # in the calling use case (PR3.3), not from this method.
        stmt = text("""
            INSERT INTO inferred_schemas (
                id, endpoint_id, integration, event_type, schema_json,
                sample_count, version, last_field_added_at, created_at, updated_at
            ) VALUES (
                :id, :endpoint_id, :integration, :event_type, CAST(:schema_json AS JSONB),
                :sample_count, :version, :last_field_added_at, :created_at, :updated_at
            )
            ON CONFLICT (endpoint_id, integration, COALESCE(event_type, ''))
            DO UPDATE SET
                schema_json = EXCLUDED.schema_json,
                sample_count = EXCLUDED.sample_count,
                version = EXCLUDED.version,
                last_field_added_at = EXCLUDED.last_field_added_at,
                updated_at = EXCLUDED.updated_at
        """)
        await self._session.execute(
            stmt,
            {
                "id": schema.id,
                "endpoint_id": schema.endpoint_id,
                "integration": schema.integration,
                "event_type": schema.event_type,
                "schema_json": json.dumps(schema.schema_json),
                "sample_count": schema.sample_count,
                "version": schema.version,
                "last_field_added_at": schema.last_field_added_at,
                "created_at": schema.created_at,
                "updated_at": schema.updated_at,
            },
        )

    async def list_by_endpoint(self, endpoint_id: UUID) -> list[InferredSchema]:
        stmt = (
            select(InferredSchemaTable)
            .where(InferredSchemaTable.endpoint_id == endpoint_id)  # type: ignore[arg-type]  # SQLAlchemy/mypy strict incompat
            .order_by(InferredSchemaTable.integration, InferredSchemaTable.event_type)  # type: ignore[arg-type]  # SQLAlchemy column descriptors at runtime
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(r) for r in rows]

    async def acquire_advisory_lock(self, key: int) -> None:
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


def _to_entity(row: InferredSchemaTable) -> InferredSchema:
    return InferredSchema(
        id=row.id,
        endpoint_id=row.endpoint_id,
        integration=row.integration,
        event_type=row.event_type,
        schema_json=row.schema_data,  # DB column "schema_json" mapped to Python attr "schema_data"
        sample_count=row.sample_count,
        version=row.version,
        last_field_added_at=row.last_field_added_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
