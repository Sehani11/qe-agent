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
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    code_reference: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    github_links: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    implementation_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
