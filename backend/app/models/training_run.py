"""TrainingRun model — one fine-tuning run, from uploads to a downloadable adapter.

A run is a long-lived, out-of-process job: it builds a dataset from the user's
uploaded corpus, pushes it to Kaggle's free GPU, waits for the kernel, and pulls
the resulting LoRA adapter back. None of that fits inside a request, so the row
IS the progress indicator — the API writes to it as each stage lands and the
client polls it.

The row therefore records enough to explain a run after the fact without going
back to Kaggle: which stage it reached, what it built, and the log it produced.
`training/RUN_LOG.md` remains the place measured *quality* is written down; this
table is the mechanical record of the job.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Waiting to be picked up. Set at creation, before the worker starts.
STATUS_QUEUED = "queued"
#: Building train.jsonl / holdout.jsonl from the user's uploaded datasets.
STATUS_BUILDING = "building"
#: Dataset and kernel pushed; Kaggle's GPU kernel is executing.
STATUS_TRAINING = "training"
#: Kernel finished; downloading its output and extracting the adapter.
STATUS_FETCHING = "fetching"
#: Adapter saved and downloadable.
STATUS_COMPLETED = "completed"
#: Stopped at some stage; `detail` says which and why.
STATUS_FAILED = "failed"

#: Statuses a run can still leave under its own power. Anything else is final,
#: which is what lets the client stop polling.
ACTIVE_STATUSES = frozenset(
    {STATUS_QUEUED, STATUS_BUILDING, STATUS_TRAINING, STATUS_FETCHING}
)


class TrainingRun(Base):
    """One end-to-end fine-tuning run kicked off from the app."""

    __tablename__ = "training_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_QUEUED, index=True
    )
    #: One line of human-readable context for `status` — the step in progress,
    #: or on failure the reason. Shown verbatim in the UI, so it must never
    #: carry a stack trace or a credential.
    detail: Mapped[str | None] = mapped_column(String, nullable=True)

    #: What the dataset build produced. Zero until the build stage finishes;
    #: kept afterwards so a completed run still says what it trained on, even
    #: though training/data is overwritten by the next run.
    train_pairs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    holdout_pairs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: The Kaggle kernel, as "owner/slug", so the UI can link to the live run.
    #: None until the push succeeds.
    kernel_ref: Mapped[str | None] = mapped_column(String, nullable=True)

    #: Where the adapter landed, relative to the repo root. None until the run
    #: completes. Relative because the absolute path differs between the host
    #: that trained and any host that later serves.
    adapter_dir: Mapped[str | None] = mapped_column(String, nullable=True)

    #: Accumulated stdout/stderr from every stage, appended as they run. This
    #: is what makes a failed run diagnosable without shell access to the host.
    log: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    #: Set once the run reaches a final status, success or failure.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
