"""add_indexed_ref_to_knowledge_sources

Revision ID: d4f1a7b93e26
Revises: c7e1a92d4b30
Create Date: 2026-08-28 00:00:00.000000

Hybrid discovery — records the commit SHA a source_type="code" index was built
at, so verification can tell a fresh index from a stale one and decline to use
the stale one rather than sending the agent to files that have since moved.
Null for every other source type, which have no notion of a version.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f1a7b93e26"
down_revision: Union[str, Sequence[str], None] = "c7e1a92d4b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable indexed_ref column to knowledge_sources."""
    op.add_column(
        "knowledge_sources",
        sa.Column("indexed_ref", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove indexed_ref column from knowledge_sources."""
    op.drop_column("knowledge_sources", "indexed_ref")
