"""VerificationResult model definition."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VerificationResult(Base):
    """Stores LLM verification results per BDD scenario."""

    __tablename__ = "verification_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    scenario_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scenario_title: Mapped[str] = mapped_column(String, nullable=False)
    # 20, not 10: "inconclusive" is 12 characters and would not fit the original
    # width, so a third verdict would have failed on insert rather than at review.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    code_reference: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    github_links: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    implementation_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    rag_context: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # The source this verdict was produced against: the mode the user chose and
    # the exact input they submitted. Stored per row, not per session, because
    # results accumulate — one session can hold several runs against different
    # sources, and attaching it to the session would relabel older verdicts.
    # Nullable: rows written before these columns have no recorded source.
    verification_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    github_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
