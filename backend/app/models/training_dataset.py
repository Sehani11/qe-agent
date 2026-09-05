"""TrainingDataset model definition (Story 6.7)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base


class TrainingDataset(Base):
    """A manually uploaded training corpus file (.feature or .jsonl).

    Deliberately NOT stored in ``bdd_files``: that table is session-scoped
    capture of what the application produced or a user corrected, and every row
    belongs to a pipeline run. A supplied corpus has no session, no ticket and
    no acceptance criteria of its own.

    The file content lives in Supabase Storage (``storage_path``); this row is
    the record, not a second copy.
    """

    __tablename__ = "training_datasets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    # "feature" (Gherkin) or "jsonl" (ready-made training pairs)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    # Scenarios for a .feature file, pairs for a .jsonl file — `kind` says which.
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Full object path inside the bucket, including the {user_id} prefix.
    storage_path: Mapped[str] = mapped_column(String, nullable=False)

    # --- Story 6.6: training-data governance ----------------------------
    # Same consent control as bdd_files. Without it this table would be a route
    # around the opt-out, since build_dataset.py reads it as a corpus source.
    # Defaults from the setting so the control FAILS CLOSED.
    training_opt_in: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=lambda: settings.training_data_opt_in,
        server_default=true(),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
