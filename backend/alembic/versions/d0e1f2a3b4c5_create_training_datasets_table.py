"""create_training_datasets_table

Story 6.7 — manually uploaded training corpora (.feature / .jsonl).

The file content lives in Supabase Storage; this table is the record of what
was uploaded, scoped per user. `training_opt_in` mirrors `bdd_files` (Story
6.6) so this table cannot become a route around the training-data opt-out —
`training/build_dataset.py` reads it as a corpus source.

RLS is NOT created here: `auth.uid()` is Supabase-specific and lives in
backend/supabase/migrations/001_rls_policies.sql, which is applied manually.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "training_datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(), nullable=False),
        # NOT NULL with a server default of true: every row is either usable for
        # training or not, and "unknown consent" is not a meaningful state.
        sa.Column(
            "training_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_training_datasets_user_id"),
        "training_datasets",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_training_datasets_user_id"), table_name="training_datasets"
    )
    op.drop_table("training_datasets")
