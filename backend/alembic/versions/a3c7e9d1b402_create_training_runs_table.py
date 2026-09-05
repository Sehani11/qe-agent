"""create_training_runs_table

One row per fine-tuning run kicked off from the app: build a dataset from the
user's uploads, push it to Kaggle's GPU, pull the LoRA adapter back.

The row is the progress indicator. A run outlives any request — the GPU kernel
alone takes tens of minutes — so the worker writes each stage here and the
client polls it. `log` keeps the stage output so a failure is diagnosable
without shell access to the host that ran it.

RLS is NOT created here: `auth.uid()` is Supabase-specific and lives in
backend/supabase/migrations/001_rls_policies.sql, which is applied manually.

Revision ID: a3c7e9d1b402
Revises: fc81d4a9e05c
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a3c7e9d1b402"
down_revision: Union[str, Sequence[str], None] = "fc81d4a9e05c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "training_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="queued"
        ),
        sa.Column("detail", sa.String(), nullable=True),
        # What the build produced, kept on the row: training/data is overwritten
        # by the next run, so a completed run could not otherwise say what it
        # trained on.
        sa.Column("train_pairs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("holdout_pairs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kernel_ref", sa.String(), nullable=True),
        # Relative to the repo root: the absolute path differs between the host
        # that trained and any host that later serves the adapter.
        sa.Column("adapter_dir", sa.String(), nullable=True),
        sa.Column("log", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_training_runs_user_id"), "training_runs", ["user_id"], unique=False
    )
    # The worker looks up active runs by status to refuse a concurrent start;
    # the list endpoint filters on user_id alone.
    op.create_index(
        op.f("ix_training_runs_status"), "training_runs", ["status"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_training_runs_status"), table_name="training_runs")
    op.drop_index(op.f("ix_training_runs_user_id"), table_name="training_runs")
    op.drop_table("training_runs")
