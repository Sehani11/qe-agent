"""BddFile model definition."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String, Text, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base


class BddFile(Base):
    """Stores generated or uploaded BDD feature file content scoped to a session."""

    __tablename__ = "bdd_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # "generated" | "uploaded" | "edited"
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    # --- Story 6.4: training-pair capture -------------------------------
    # The acceptance criteria that produced this content. Stored per row rather
    # than on `sessions` because a session can regenerate after its Jira ticket
    # has been edited — a session-level column would leave older rows pointing
    # at text that no longer produced them.
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "json" (generated: a serialized BDDGenerateResponse) or "gherkin"
    # (uploaded / edited: raw .feature text). The content column has always
    # held both; this records which so consumers need not infer it from source.
    content_format: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # For source="edited": the generated row this edit derives from. Resolved
    # server-side. NULL when the user edited uploaded content with no generated
    # ancestor.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # --- Story 6.6: training-data governance ----------------------------
    # Whether this row may be used to fine-tune a model. Stamped from
    # TRAINING_DATA_OPT_IN at write time, so changing that setting later never
    # reclassifies rows that already exist. NOT NULL — every row is either
    # usable or not; "unknown" is not a meaningful state.
    #
    # The Python-side default reads the setting so this control FAILS CLOSED: a
    # write path that forgets to pass the flag inherits the deployment's actual
    # choice instead of silently defaulting to "yes, train on this". The
    # server_default only covers rows inserted outside the ORM.
    training_opt_in: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=lambda: settings.training_data_opt_in,
        server_default=true(),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
