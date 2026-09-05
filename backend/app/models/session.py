"""Session model definition."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Session(Base):
    """Stores information about a user's Jira ticket session."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # The project this session belongs to. Nullable ONLY so the migration can
    # add the column and backfill before enforcing it; every row has one after
    # that, and the API always sets it.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    jira_ticket_id: Mapped[str] = mapped_column(String, nullable=False)
    # What the user actually submitted to /ingestion/ingest — a full Jira URL or
    # a bare key. Kept alongside the extracted key so revisiting a session can
    # put the original text back in the ticket field instead of an empty box.
    # Nullable: rows created before this column, and sessions created by the BDD
    # upload path, have no submitted text.
    jira_ticket_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
