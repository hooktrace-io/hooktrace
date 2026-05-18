"""trace_summary

Revision ID: d397d6d2abcb
Revises: c0073b4e15ab
Create Date: 2026-05-18 17:10:19.957966

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d397d6d2abcb"
down_revision: str | Sequence[str] | None = "c0073b4e15ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("trace_summary", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("requests", "trace_summary")
