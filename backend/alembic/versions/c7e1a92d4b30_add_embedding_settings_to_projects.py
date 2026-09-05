"""add_embedding_settings_to_projects

Which vendor embeds a project's text, and which Pinecone index those vectors go
in, move from deployment-wide environment config onto the project row.

They are one setting in two halves. An index is built with exactly one embedding
model, and a query vector is only comparable to vectors produced by that same
model — so the provider and the index it writes to must always agree. Keeping
them together on the project is what makes that enforceable: a project's write
path and its read path resolve from the same row.

Both columns default to the empty string, meaning "no project preference": the
resolver falls back to EMBEDDING_PROVIDER / PINECONE_INDEX_NAME. Existing rows
therefore keep behaving exactly as they do today, and no vectors move — this
migration is additive and touches nothing outside the `projects` table.

A project that later switches provider needs a NEW index at the matching
dimension (openai/1536, voyage/1024) and a re-ingest; there is nothing to
migrate here because the old vectors are simply not comparable to the new ones.

Revision ID: c7e1a92d4b30
Revises: b4d8e2f1a907
Create Date: 2026-08-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e1a92d4b30"
down_revision: str | Sequence[str] | None = "b4d8e2f1a907"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default alongside the model's default: the model's applies to rows
    # this application inserts, the server's to the rows that already exist and
    # to anything writing outside the ORM. NOT NULL without it would fail on a
    # non-empty table.
    op.add_column(
        "projects",
        sa.Column(
            "embedding_provider",
            sa.String(),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "pinecone_index_name",
            sa.String(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "pinecone_index_name")
    op.drop_column("projects", "embedding_provider")
