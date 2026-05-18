"""replay

Revision ID: c0073b4e15ab
Revises: f88fffb1c697
Create Date: 2026-05-18 16:42:55.744104

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c0073b4e15ab"
down_revision: str | Sequence[str] | None = "f88fffb1c697"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "replays",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=True),
        sa.Column("response_body_preview", sa.Text(), nullable=True),
        sa.Column("response_headers", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_replays_request_id", "replays", ["request_id"])
    op.execute("""
        ALTER TABLE replays
        ADD CONSTRAINT replays_outcome_xor
        CHECK ((status_code IS NOT NULL) <> (error IS NOT NULL))
    """)


def downgrade() -> None:
    op.drop_table("replays")
