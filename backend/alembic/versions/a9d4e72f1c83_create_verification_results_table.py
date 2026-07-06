"""create_verification_results_table

Revision ID: a9d4e72f1c83
Revises: b37b53deea53
Create Date: 2026-04-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a9d4e72f1c83"
down_revision: Union[str, Sequence[str], None] = "b37b53deea53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "verification_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_title", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("code_reference", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("github_links", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("implementation_suggestion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_verification_results_session_id"),
        "verification_results",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_verification_results_session_id"),
        table_name="verification_results",
    )
    op.drop_table("verification_results")
