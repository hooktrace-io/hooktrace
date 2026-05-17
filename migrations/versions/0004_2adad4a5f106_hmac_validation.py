"""hmac_validation

Revision ID: 2adad4a5f106
Revises: 5058fb3e1c3e
Create Date: 2026-05-17 18:07:08.278928

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2adad4a5f106"
down_revision: str | Sequence[str] | None = "5058fb3e1c3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # endpoints: signature config per endpoint
    op.add_column("endpoints", sa.Column("signature_provider", sa.Text(), nullable=True))
    op.add_column(
        "endpoints",
        sa.Column("signature_secret_encrypted", sa.LargeBinary(), nullable=True),
    )

    # requests: signature status captured at ingest time
    op.add_column("requests", sa.Column("signature_status", sa.Text(), nullable=True))

    # Backfill existing rows : signature_status defaults to 'no_provider' for
    # ALL pre-PR1 captures (no signature config existed before this migration,
    # so by definition every legacy row falls in the "no provider configured"
    # bucket). Without this backfill, PR2's aggregation SQL fails with
    # "field name must not be null" when it does json_object_agg(NULL, ...).
    op.execute(
        "UPDATE requests SET signature_status = 'no_provider' "
        "WHERE signature_status IS NULL"
    )


def downgrade() -> None:
    op.drop_column("requests", "signature_status")
    op.drop_column("endpoints", "signature_secret_encrypted")
    op.drop_column("endpoints", "signature_provider")
