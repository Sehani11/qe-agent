"""add_serving_state_to_training_runs

A finished run leaves a PEFT adapter on disk and stops there. Getting it into
the local model runtime was a manual three-step — convert the adapter to GGUF,
write a Modelfile, register it — so a run could be "completed" while the model
it produced was not servable, and nothing recorded the difference.

These three columns are that difference. They are deliberately separate from
`status` / `detail`: those belong to the Kaggle job and must keep saying how the
training itself went, even after a later publish fails. A run that trained fine
and published badly is a real state, and one pair of columns cannot say it.

All nullable with no backfill: every run that predates this genuinely has no
publish attempt, and a default of "not served" would be indistinguishable from
one that was tried and failed.

Revision ID: a7c2e9f10b34
Revises: d4f1a7b93e26
Create Date: 2026-09-26 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c2e9f10b34"
down_revision: str | Sequence[str] | None = "d4f1a7b93e26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "training_runs",
        sa.Column("serve_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "training_runs",
        sa.Column("serve_detail", sa.String(), nullable=True),
    )
    op.add_column(
        "training_runs",
        sa.Column("served_model", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("training_runs", "served_model")
    op.drop_column("training_runs", "serve_detail")
    op.drop_column("training_runs", "serve_status")
