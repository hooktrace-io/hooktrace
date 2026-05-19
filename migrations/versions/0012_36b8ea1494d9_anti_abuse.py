"""anti_abuse

Revision ID: 36b8ea1494d9
Revises: c8de2dab6bba
Create Date: 2026-05-19 09:00:00.000000

Adds anti-abuse fields to endpoints (PR10 Block A):
- flagged_at: timestamp set when an endpoint is flagged for abuse review.
- flag_reason: bounded enum-as-CHECK ('phishing_no_forward',
  'slug_denylist_postcreation', 'manual_review').
- partial index 'endpoints_unflagged_idx' over (id) WHERE flagged_at IS NULL,
  so scans for active (unflagged) endpoints stay cheap as flagged rows
  accumulate.

Purely additive on top of migration 0011.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "36b8ea1494d9"
down_revision: str | Sequence[str] | None = "c8de2dab6bba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "endpoints",
        sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "endpoints",
        sa.Column("flag_reason", sa.Text(), nullable=True),
    )
    op.execute("""
        ALTER TABLE endpoints
        ADD CONSTRAINT endpoints_flag_reason_check
        CHECK (
            flag_reason IS NULL
            OR flag_reason IN ('phishing_no_forward', 'slug_denylist_postcreation', 'manual_review')
        )
    """)
    op.create_index(
        "endpoints_unflagged_idx",
        "endpoints",
        ["id"],
        postgresql_where=sa.text("flagged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("endpoints_unflagged_idx", table_name="endpoints")
    op.execute("ALTER TABLE endpoints DROP CONSTRAINT endpoints_flag_reason_check")
    op.drop_column("endpoints", "flag_reason")
    op.drop_column("endpoints", "flagged_at")
