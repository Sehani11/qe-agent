"""create_evaluation_results_table

Story 6.3 — fine-tuned vs general-LLM comparison runs.

One row per (run_id, item_id, configured_provider). Metrics are computed as
pure functions over these rows rather than stored, so scoring can be revised
and re-run without re-generating — which matters when one fine-tuned generation
costs ~45s on non-GPU hardware.

`effective_provider` is the load-bearing column: the fine-tuned provider falls
back to the general LLM silently and returns an identically-shaped payload, so
without per-row attribution an entire run could be general-vs-general and look
healthy.

RLS is NOT created here: `auth.uid()` is Supabase-specific and lives in
backend/supabase/migrations/001_rls_policies.sql, which is applied manually.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-13 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("domain_group", sa.String(16), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False),
        sa.Column("configured_provider", sa.String(32), nullable=False),
        sa.Column("effective_provider", sa.String(32), nullable=True),
        sa.Column("fallback_reason", sa.String(64), nullable=True),
        sa.Column("model_identifier", sa.String(), nullable=False),
        sa.Column("scenarios", postgresql.JSONB(), nullable=True),
        sa.Column("reference_scenarios", postgresql.JSONB(), nullable=True),
        sa.Column("latency_seconds", sa.Float(), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_evaluation_results_run_id", "evaluation_results", ["run_id"]
    )
    op.create_index(
        "ix_evaluation_results_item_id", "evaluation_results", ["item_id"]
    )
    # `--resume` asks exactly this question: has (run, item, provider) been done?
    # A unique index makes the answer cheap and makes a double-write impossible
    # if two runners are ever pointed at the same run_id.
    op.create_index(
        "uq_evaluation_results_run_item_provider",
        "evaluation_results",
        ["run_id", "item_id", "configured_provider"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_evaluation_results_run_item_provider", table_name="evaluation_results"
    )
    op.drop_index("ix_evaluation_results_item_id", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_run_id", table_name="evaluation_results")
    op.drop_table("evaluation_results")
