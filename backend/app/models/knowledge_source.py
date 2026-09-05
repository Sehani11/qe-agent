"""KnowledgeSource model definition."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeSource(Base):
    """Tracks project knowledge sources ingested into the vector knowledge base."""

    __tablename__ = "knowledge_sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # The project whose knowledge base this source belongs to. Sources are
    # embedded into a per-project Pinecone namespace, so listing them without
    # this filter would offer to delete vectors that live somewhere else.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    # The Pinecone vector id prefix for this source: the page id (confluence),
    # ticket id (jira) or generated uuid (document). Vectors are stored with ids
    # f"{source_type}_{source_ref}_chunk_{i}" — needed to delete them (Story 4.7).
    # Nullable for rows created before this column and for pre-embed failures.
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    source_url: Mapped[str] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=True)
    # The commit SHA a source_type="code" index was built at. Null on every
    # other source type, which have no notion of a version.
    #
    # It is what makes the index checkable: a branch name moves, so recording
    # "main" cannot distinguish an index built an hour ago from one built a
    # month ago. Verification compares this against the head of the ref it is
    # about to read and declines to use a stale index rather than sending the
    # agent to files that have since moved.
    indexed_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    ingestion_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
