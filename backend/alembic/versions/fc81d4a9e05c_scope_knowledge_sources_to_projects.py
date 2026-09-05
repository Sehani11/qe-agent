"""scope_knowledge_sources_to_projects

Knowledge moves from one namespace per user to one per project. Each
`knowledge_sources` row records which project's base it was ingested into, so
the sources list — and the delete that reaches into Pinecone from it — cannot
act on vectors that live in a different namespace.

Existing rows are backfilled to the user's Project-1, matching where the
earlier migration put their sessions. Their VECTORS are not moved by this
migration: Pinecone lives outside the database and cannot be updated in a
transaction with it. Run `scripts/migrate_knowledge_vectors.py` afterwards to
copy them into the per-project namespace, or re-ingest.

`project_id` stays NULLABLE, unlike `sessions.project_id`. A source can be
written by a path that has no project (an older client), and an ingestion that
failed before embedding leaves a row worth keeping; forcing a project onto
those would invent an association the vectors do not have.

Revision ID: fc81d4a9e05c
Revises: 8c4760c0942a
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fc81d4a9e05c"
down_revision: str | Sequence[str] | None = "8c4760c0942a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the project scope and point existing sources at Project-1."""
    op.add_column(
        "knowledge_sources",
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_knowledge_sources_project_id", "knowledge_sources", ["project_id"]
    )
    op.create_foreign_key(
        "fk_knowledge_sources_project_id",
        "knowledge_sources",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Same destination the sessions backfill used, so a migrated user finds
    # their knowledge in the project their history already lives in.
    op.execute(
        """
        UPDATE knowledge_sources
        SET project_id = p.id
        FROM projects p
        WHERE p.user_id = knowledge_sources.user_id
          AND p.name = 'Project-1'
          AND knowledge_sources.project_id IS NULL
        """
    )


def downgrade() -> None:
    """Drop the project scope. Sources and their vectors are untouched."""
    op.drop_constraint(
        "fk_knowledge_sources_project_id", "knowledge_sources", type_="foreignkey"
    )
    op.drop_index("ix_knowledge_sources_project_id", table_name="knowledge_sources")
    op.drop_column("knowledge_sources", "project_id")
