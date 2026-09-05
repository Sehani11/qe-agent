"""add_training_capture_to_bdd_files

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-08 00:00:00.000000

Story 6.4 — captures the input half of a training pair. Adds:
  * acceptance_criteria: the AC text that produced this BDD content
  * content_format:      'json' | 'gherkin' — the column was already
                         heterogeneous (generated rows hold JSON, uploaded
                         rows hold raw Gherkin) with nothing recording which
  * parent_id:           the generated row an edited row derives from

All nullable: historic rows have no acceptance criteria and never will.
content_format IS derivable for existing rows, so it is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add training-capture columns to bdd_files and backfill content_format."""
    op.add_column(
        "bdd_files",
        sa.Column("acceptance_criteria", sa.Text(), nullable=True),
    )
    op.add_column(
        "bdd_files",
        sa.Column("content_format", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "bdd_files",
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Existing rows: the format is implied by source, so it can be recovered.
    # acceptance_criteria cannot be recovered and is deliberately left NULL
    # rather than guessed.
    op.execute(
        """
        UPDATE bdd_files
        SET content_format = CASE
            WHEN source = 'generated' THEN 'json'
            ELSE 'gherkin'
        END
        WHERE content_format IS NULL
        """
    )


def downgrade() -> None:
    """Remove training-capture columns from bdd_files."""
    op.drop_column("bdd_files", "parent_id")
    op.drop_column("bdd_files", "content_format")
    op.drop_column("bdd_files", "acceptance_criteria")
