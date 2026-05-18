"""integration_detection

Revision ID: 4113bf01bbf7
Revises: 2adad4a5f106
Create Date: 2026-05-18 11:26:08.561952

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4113bf01bbf7"
down_revision: str | Sequence[str] | None = "2adad4a5f106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("requests", sa.Column("detected_integration", sa.Text(), nullable=True))
    op.add_column("requests", sa.Column("detected_event_type", sa.Text(), nullable=True))
    op.execute("""
        ALTER TABLE requests
        ADD CONSTRAINT requests_detected_integration_check
        CHECK (
            detected_integration IS NULL
            OR detected_integration IN (
                'stripe', 'github', 'shopify', 'twilio', 'mailgun',
                'discord', 'slack', 'zapier', 'n8n'
            )
        )
    """)
    op.create_index(
        "requests_integration_recent_idx",
        "requests",
        ["endpoint_id", "detected_integration", sa.text("received_at DESC")],
        postgresql_where=sa.text("detected_integration IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("requests_integration_recent_idx", table_name="requests")
    op.execute("ALTER TABLE requests DROP CONSTRAINT requests_detected_integration_check")
    op.drop_column("requests", "detected_event_type")
    op.drop_column("requests", "detected_integration")
