from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, Computed, LargeBinary, SmallInteger
from sqlalchemy.dialects.postgresql import INET, JSONB, TSVECTOR
from sqlmodel import Field, SQLModel


class EndpointTable(SQLModel, table=True):
    __tablename__ = "endpoints"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    token: str = Field(unique=True, index=True, nullable=False)
    created_at: datetime = Field(nullable=False)
    expires_at: datetime = Field(nullable=False, index=True)
    request_count: int = Field(default=0, nullable=False)

    # V2 — custom response
    response_status_code: int = Field(default=200, nullable=False)
    response_body: str = Field(default='{"ok":true}', nullable=False)
    response_headers: dict[str, str] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    response_delay_ms: int = Field(default=0, nullable=False)

    # V3 — HMAC signature validation
    signature_provider: str | None = Field(default=None)
    signature_secret_encrypted: bytes | None = Field(
        default=None, sa_column=Column(LargeBinary, nullable=True)
    )

    # V3 — forward config (Block 1 of PR7+PR8)
    forward_url: str | None = Field(default=None)
    forward_headers: dict[str, str] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    forward_secret_encrypted: bytes | None = Field(
        default=None, sa_column=Column(LargeBinary, nullable=True)
    )


class RequestTable(SQLModel, table=True):
    __tablename__ = "requests"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    endpoint_id: UUID = Field(foreign_key="endpoints.id", nullable=False, index=True)
    method: str = Field(nullable=False)
    path: str = Field(nullable=False)
    query_string: str | None = Field(default=None)
    headers: dict[str, str] = Field(sa_column=Column(JSONB, nullable=False))
    body_preview: str | None = Field(default=None)
    body_size: int = Field(nullable=False)
    blob_key: str | None = Field(default=None)
    source_ip: str = Field(sa_column=Column(INET, nullable=False))
    received_at: datetime = Field(nullable=False)

    # V3 — HMAC signature validation
    signature_status: str | None = Field(default=None)

    # V3 — integration detection (PR2)
    detected_integration: str | None = Field(default=None)
    detected_event_type: str | None = Field(default=None)

    # V2.5 — generated tsvector column for full-text search. Mirrors the
    # GENERATED ALWAYS expression in migration 0003 so SQLAlchemy:
    #   - never tries to INSERT/UPDATE this column (Computed handles it),
    #   - can recreate the column in tests via SQLModel.metadata.create_all().
    search_vector: str | None = Field(
        default=None,
        sa_column=Column(
            "search_vector",
            TSVECTOR,
            Computed(
                "to_tsvector('simple', "
                "coalesce(method, '') || ' ' || "
                "coalesce(path, '') || ' ' || "
                "coalesce(body_preview, '') || ' ' || "
                "coalesce(headers::text, ''))",
                persisted=True,
            ),
            nullable=True,
        ),
    )


class ReplayTable(SQLModel, table=True):
    __tablename__ = "replays"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    request_id: UUID = Field(foreign_key="requests.id", index=True)
    target_url: str
    status_code: int | None = Field(default=None, sa_column=Column(SmallInteger, nullable=True))
    response_body_preview: str | None = None
    response_headers: dict[str, str] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    error: str | None = None
    duration_ms: int
    attempted_at: datetime


class ForwardTable(SQLModel, table=True):
    __tablename__ = "forwards"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    request_id: UUID = Field(foreign_key="requests.id", nullable=False)
    endpoint_id: UUID = Field(foreign_key="endpoints.id", nullable=False)
    target_url: str = Field(nullable=False)
    status: str = Field(nullable=False)
    attempt_count: int = Field(default=0, nullable=False)
    last_attempt_at: datetime | None = Field(default=None)
    next_attempt_at: datetime | None = Field(default=None)
    final_status_code: int | None = Field(default=None)
    final_error: str | None = Field(default=None)
    forward_started_at: datetime | None = Field(default=None)
    forward_completed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(nullable=False)
    # V3 — set when an operator manually retries a forward from the DLQ UI.
    # Audit trail only; never reset to None once set.
    manual_retry_at: datetime | None = Field(default=None)
