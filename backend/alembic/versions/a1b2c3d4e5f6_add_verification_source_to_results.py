"""add_verification_source_to_verification_results

Records WHICH code the verdict was produced against: the mode the user chose
and the exact GitHub input they submitted (a repo URL, a PR URL, or a newline
separated list of file URLs).

Without this, revisiting a past session showed verdicts with no way to tell
what they were checked against — and the workspace could not restore the source
field, so re-running a past session meant retyping the URL from memory. The
verdict's own `github_links` are links the LLM cited for individual scenarios,
not the source the run was scoped to, so they cannot stand in for this.

Stored per ROW rather than per session because verification results accumulate
(nothing deletes earlier rows), so one session can hold several runs against
different sources. Attaching the source to the session would make the newest run
silently relabel every older verdict.

Nullable with no backfill: rows written before this column genuinely have no
recorded source, and an empty string would be indistinguishable from a run
against an empty input.

RLS is NOT created here: `auth.uid()` is Supabase-specific and lives in
backend/supabase/migrations/001_rls_policies.sql, which is applied manually.

Revision ID: a1b2c3d4e5f6
Revises: f6b7c8d9e0a1
Create Date: 2026-08-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f6b7c8d9e0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the verification source (mode + submitted input) to result rows."""
    op.add_column(
        "verification_results",
        sa.Column("verification_mode", sa.String(length=20), nullable=True),
    )
    # Text, not String: exact-files mode submits one URL per line and a repo can
    # contribute many, so this has no meaningful length bound.
    op.add_column(
        "verification_results",
        sa.Column("github_input", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop the verification source columns."""
    op.drop_column("verification_results", "github_input")
    op.drop_column("verification_results", "verification_mode")
