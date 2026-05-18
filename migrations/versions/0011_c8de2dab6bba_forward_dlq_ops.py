"""forward_dlq_ops

Revision ID: c8de2dab6bba
Revises: 72e54acb55c0
Create Date: 2026-05-18 20:00:00.000000

Extends the forwards table for DLQ (dead letter queue) operations:
- adds 'abandoned' status (soft-delete by operator) to the status CHECK
- adds manual_retry_at column (audit trail for manual retries from the UI)

Purely additive on top of migration 0010 (forwards table).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8de2dab6bba"
down_revision: str | Sequence[str] | None = "72e54acb55c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE forwards DROP CONSTRAINT forwards_status_check")
    op.execute("""
        ALTER TABLE forwards ADD CONSTRAINT forwards_status_check
        CHECK (status IN ('pending', 'in_flight', 'succeeded', 'failed', 'dead', 'abandoned'))
    """)
    op.add_column(
        "forwards",
        sa.Column("manual_retry_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forwards", "manual_retry_at")
    # Defensive: convert 'abandoned' → 'dead' before re-narrowing the CHECK,
    # otherwise the constraint re-add fails on existing rows.
    op.execute("UPDATE forwards SET status = 'dead' WHERE status = 'abandoned'")
    op.execute("ALTER TABLE forwards DROP CONSTRAINT forwards_status_check")
    op.execute("""
        ALTER TABLE forwards ADD CONSTRAINT forwards_status_check
        CHECK (status IN ('pending', 'in_flight', 'succeeded', 'failed', 'dead'))
    """)
