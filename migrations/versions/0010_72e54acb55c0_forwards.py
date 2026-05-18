"""forwards

Revision ID: 72e54acb55c0
Revises: 9a1f0c4b5d7e
Create Date: 2026-05-18 19:34:20.480591

Adds forward config columns to endpoints and the forwards table for
per-request forward attempt tracking (Block 1 of PR7+PR8).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "72e54acb55c0"
down_revision: str | Sequence[str] | None = "9a1f0c4b5d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # endpoints: forward config per endpoint
    op.add_column("endpoints", sa.Column("forward_url", sa.Text(), nullable=True))
    op.add_column("endpoints", sa.Column("forward_headers", postgresql.JSONB(), nullable=True))
    op.add_column(
        "endpoints", sa.Column("forward_secret_encrypted", sa.LargeBinary(), nullable=True)
    )

    # forwards: one row per (request × forward attempt outcome).
    op.create_table(
        "forwards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_status_code", sa.Integer(), nullable=True),
        sa.Column("final_error", sa.Text(), nullable=True),
        sa.Column("forward_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("forward_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_flight', 'succeeded', 'failed', 'dead')",
            name="forwards_status_check",
        ),
    )
    op.create_index(
        "forwards_dead_idx",
        "forwards",
        ["forward_completed_at"],
        postgresql_where=sa.text("status = 'dead'"),
    )
    op.create_index(
        "forwards_endpoint_idx",
        "forwards",
        ["endpoint_id", "created_at"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("forwards_endpoint_idx", table_name="forwards")
    op.drop_index("forwards_dead_idx", table_name="forwards")
    op.drop_table("forwards")
    op.drop_column("endpoints", "forward_secret_encrypted")
    op.drop_column("endpoints", "forward_headers")
    op.drop_column("endpoints", "forward_url")
