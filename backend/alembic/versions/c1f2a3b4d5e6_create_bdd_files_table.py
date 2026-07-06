"""create_bdd_files_table

Revision ID: c1f2a3b4d5e6
Revises: a9d4e72f1c83
Create Date: 2026-04-11 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c1f2a3b4d5e6"
down_revision: Union[str, Sequence[str], None] = "a9d4e72f1c83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "bdd_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_bdd_files_session_id"),
        "bdd_files",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bdd_files_user_id"),
        "bdd_files",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_bdd_files_user_id"), table_name="bdd_files")
    op.drop_index(op.f("ix_bdd_files_session_id"), table_name="bdd_files")
    op.drop_table("bdd_files")
