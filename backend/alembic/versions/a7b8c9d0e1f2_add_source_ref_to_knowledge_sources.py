"""add_source_ref_to_knowledge_sources

Revision ID: a7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-07-05 00:00:00.000000

Story 4.7 — stores the Pinecone vector-id prefix (Confluence page id / Jira
ticket id / uploaded-document uuid) so a source's vectors can be deleted.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable source_ref column to knowledge_sources."""
    op.add_column(
        "knowledge_sources",
        sa.Column("source_ref", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove source_ref column from knowledge_sources."""
    op.drop_column("knowledge_sources", "source_ref")
