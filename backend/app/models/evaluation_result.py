"""EvaluationResult model definition (Story 6.3).

One row per (run_id, item_id, configured_provider): the raw generation from one
provider for one evaluation item. Metrics are NOT stored here — they are
computed as pure functions over these rows, so a scoring change can be re-run
without re-generating, which matters when a fine-tuned generation costs ~45s.

The attribution columns are the reason this table can be trusted.
`FineTunedModelProvider` enforces a 12s bound and falls back to the general LLM
on every call at the measured latency, returning output that is byte-identical
in shape. Without `effective_provider` recorded per row, a whole evaluation run
could silently be general-LLM-versus-general-LLM and look perfectly healthy.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EvaluationResult(Base):
    """A single provider's generation for a single evaluation item."""

    __tablename__ = "evaluation_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Groups one execution of the pipeline, so runs stay comparable over time
    # and `--resume` can tell finished work from work still to do.
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)

    item_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    domain_group: Mapped[str] = mapped_column(String(16), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False)

    # What we asked for vs what actually answered. These differing is not an
    # error to be swallowed — it is the finding that invalidates a comparison.
    configured_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_identifier: Mapped[str] = mapped_column(String, nullable=False)

    scenarios: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    reference_scenarios: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)

    latency_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    @property
    def is_trustworthy(self) -> bool:
        """Whether this row measures the provider it claims to measure.

        A row configured `fine_tuned` but served by `general_llm` is real data
        about a degradation and useless data about the fine-tune. Metrics must
        exclude it rather than average it in.
        """
        return (
            self.succeeded
            and self.effective_provider is not None
            and self.effective_provider == self.configured_provider
        )
