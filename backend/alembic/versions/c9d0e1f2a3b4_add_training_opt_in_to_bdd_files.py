"""add_training_opt_in_to_bdd_files

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-08 00:00:00.000000

Story 6.6 — records whether a captured row may be used to fine-tune a model.

NOT NULL with a server default of true: "unknown consent" is not a meaningful
state, and the server default both backfills existing rows in one step and keeps
any raw INSERT in a valid state. Existing rows were captured under the previous
always-on behaviour, so true is the correct value for them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add training_opt_in to bdd_files, defaulting existing rows to opted-in."""
    op.add_column(
        "bdd_files",
        sa.Column(
            "training_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    """Remove training_opt_in from bdd_files."""
    op.drop_column("bdd_files", "training_opt_in")
