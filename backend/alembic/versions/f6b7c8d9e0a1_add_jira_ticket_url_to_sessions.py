"""add_jira_ticket_url_to_sessions

Records the text the user actually submitted when ingesting a ticket (a full
Jira URL, or a bare key when that is what they typed). The extracted key alone
cannot be turned back into the URL it came from — the base URL may have come
from the pasted link rather than JIRA_BASE_URL — so revisiting a session had no
way to restore the ticket field.

Nullable with no backfill: sessions created before this column, and sessions
created by the BDD upload path, genuinely have no submitted text, and an empty
string would be indistinguishable from one the user cleared.

RLS is NOT created here: `auth.uid()` is Supabase-specific and lives in
backend/supabase/migrations/001_rls_policies.sql, which is applied manually.

Revision ID: f6b7c8d9e0a1
Revises: e1f2a3b4c5d6
Create Date: 2026-08-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6b7c8d9e0a1"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the submitted ticket URL / key to sessions."""
    op.add_column(
        "sessions",
        sa.Column("jira_ticket_url", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Drop the submitted ticket URL / key from sessions."""
    op.drop_column("sessions", "jira_ticket_url")
