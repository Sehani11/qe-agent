"""widen_verification_status_for_inconclusive

Verdicts gained a third value, "inconclusive", for a run that could not gather
evidence either way. At 12 characters it does not fit the original VARCHAR(10),
so without this the new status fails on INSERT — after the scenario has already
been streamed to the user, which is the worst place to discover it.

Widening is not destructive: every existing value keeps its exact contents, and
the downgrade is safe only while no row holds a value longer than 10 characters,
so it deletes nothing and instead refuses to run if such rows exist.

Revision ID: b4d8e2f1a907
Revises: a3c7e9d1b402
Create Date: 2026-08-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d8e2f1a907"
down_revision: Union[str, Sequence[str], None] = "a3c7e9d1b402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen verification_results.status from VARCHAR(10) to VARCHAR(20)."""
    op.alter_column(
        "verification_results",
        "status",
        existing_type=sa.String(length=10),
        type_=sa.String(length=20),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Narrow status back to VARCHAR(10).

    Refuses rather than truncating: a silently shortened verdict ("inconclus")
    is worse than a failed migration, because nothing downstream would flag it.
    """
    connection = op.get_bind()
    too_long = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM verification_results "
            "WHERE char_length(status) > 10"
        )
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"{too_long} verification_results row(s) hold a status longer than "
            "10 characters (e.g. 'inconclusive'). Resolve or delete those rows "
            "before downgrading, so no verdict is silently truncated."
        )

    op.alter_column(
        "verification_results",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
