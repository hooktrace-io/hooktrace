"""schema_inference

Revision ID: f88fffb1c697
Revises: 4113bf01bbf7
Create Date: 2026-05-18 15:10:13.461608

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f88fffb1c697"
down_revision: str | Sequence[str] | None = "4113bf01bbf7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inferred_schemas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("integration", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("schema_json", postgresql.JSONB(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_field_added_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute("""
        CREATE UNIQUE INDEX inferred_schemas_key_idx
        ON inferred_schemas (endpoint_id, integration, COALESCE(event_type, ''))
    """)
    # Keep the IN (...) list in sync with INTEGRATION_NAMES in
    # domain/services/integration_detector.py — drift will surface as
    # an INSERT-time constraint failure rather than silent corruption.
    op.execute("""
        ALTER TABLE inferred_schemas
        ADD CONSTRAINT inferred_schemas_integration_check
        CHECK (integration IN (
            'stripe', 'github', 'shopify', 'twilio', 'mailgun',
            'discord', 'slack', 'zapier', 'n8n'
        ))
    """)
    op.add_column("requests", sa.Column("schema_drift", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("requests", "schema_drift")
    op.execute(
        "ALTER TABLE inferred_schemas DROP CONSTRAINT inferred_schemas_integration_check"
    )
    op.drop_index("inferred_schemas_key_idx", table_name="inferred_schemas")
    op.drop_table("inferred_schemas")
