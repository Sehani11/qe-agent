"""add_projects_and_scope_sessions

Sessions become project-scoped. A project carries the settings that were
previously global environment variables — which Jira and Confluence this work
lives in, which repository to verify against, which model to default to — so
one deployment can serve several bodies of work.

Credentials are stored ENCRYPTED, never hashed: they are replayed to Jira,
Confluence and GitHub on every call, so the plaintext has to be recoverable.
See `app.core.crypto`. Nothing is encrypted *here* — the columns land empty and
are filled through the API, which is the only place the key is used.

LLM API keys are deliberately absent. They are billing credentials for the
deployment, not per-project integration secrets, and stay in the environment.
Only the provider/model CHOICE is per project.

Existing sessions are moved into a "Project-1" created per distinct user_id, so
nobody loses history. `project_id` is added nullable, backfilled, then made NOT
NULL in the same migration — the column can only be enforced once every row has
a value.

RLS is NOT created here: `auth.uid()` is Supabase-specific and lives in
backend/supabase/migrations/001_rls_policies.sql, which is applied manually.

Revision ID: 8c4760c0942a
Revises: a1b2c3d4e5f6
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c4760c0942a"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create projects, scope sessions to them, and rehome existing sessions."""
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        # No index=True here: the index is created explicitly below so it has a
        # predictable name the downgrade can drop. Doing both makes PostgreSQL
        # refuse the second CREATE INDEX as a duplicate.
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "jira_base_url", sa.String(), nullable=False, server_default=""
        ),
        sa.Column(
            "jira_user_email", sa.String(), nullable=False, server_default=""
        ),
        # Text, not String: Fernet ciphertext is meaningfully longer than the
        # token it wraps, and a length cap here would truncate silently.
        sa.Column(
            "jira_api_token_encrypted", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "confluence_base_url", sa.String(), nullable=False, server_default=""
        ),
        sa.Column(
            "confluence_user_email", sa.String(), nullable=False, server_default=""
        ),
        sa.Column(
            "confluence_api_token_encrypted",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("github_repo", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "github_access_token_encrypted",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("llm_provider", sa.String(), nullable=False, server_default=""),
        sa.Column("llm_model", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    op.add_column(
        "sessions",
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=True),
    )

    # One Project-1 per user who already has sessions. gen_random_uuid() is
    # available on Supabase (pgcrypto); the INSERT ... SELECT keeps the whole
    # backfill in the database rather than round-tripping every row.
    op.execute(
        """
        INSERT INTO projects (id, user_id, name)
        SELECT gen_random_uuid(), s.user_id, 'Project-1'
        FROM (SELECT DISTINCT user_id FROM sessions) AS s
        """
    )
    op.execute(
        """
        UPDATE sessions
        SET project_id = p.id
        FROM projects p
        WHERE p.user_id = sessions.user_id
          AND p.name = 'Project-1'
          AND sessions.project_id IS NULL
        """
    )

    # Only now can it be enforced: before the backfill every existing row would
    # have violated it.
    op.alter_column("sessions", "project_id", nullable=False)
    op.create_index("ix_sessions_project_id", "sessions", ["project_id"])
    op.create_foreign_key(
        "fk_sessions_project_id",
        "sessions",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Detach sessions from projects and drop the table.

    Sessions survive; only the grouping is lost. The projects rows themselves
    are dropped, so any credentials entered through the API go with them — they
    are encrypted and unrecoverable without the table anyway.
    """
    op.drop_constraint("fk_sessions_project_id", "sessions", type_="foreignkey")
    op.drop_index("ix_sessions_project_id", table_name="sessions")
    op.drop_column("sessions", "project_id")
    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")
