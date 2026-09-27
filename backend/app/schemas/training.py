"""Pydantic schemas for manual training-dataset upload (Story 6.7)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TrainingDatasetResponse(BaseModel):
    """One uploaded training corpus file.

    `training_opt_in` is deliberately absent: it is operator configuration
    stamped at write time, not user data (Story 6.6 precedent).
    """

    id: uuid.UUID
    filename: str
    kind: str = Field(..., description="'feature' (Gherkin) or 'jsonl' (pairs)")
    item_count: int = Field(
        ..., description="Scenarios for a .feature file, pairs for a .jsonl file"
    )
    storage_path: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RejectedFile(BaseModel):
    """A file that was not accepted, and why."""

    filename: str
    reason: str = Field(
        ...,
        description=(
            "Human-readable rejection reason, shown verbatim in the UI. For "
            "JSONL failures this names the offending line number."
        ),
    )


class TrainingUploadResponse(BaseModel):
    """Per-file outcomes for one multipart upload.

    Files are validated independently so a single bad file in a batch of twenty
    does not cost the user the other nineteen.
    """

    accepted: list[TrainingDatasetResponse] = []
    rejected: list[RejectedFile] = []


class ServingReadiness(BaseModel):
    """Whether this server can publish a finished adapter to a model runtime.

    Separate from `TrainingReadiness` because the prerequisites are unrelated:
    training needs Kaggle credentials and a network, publishing needs a local
    converter toolchain and a runtime on this machine. A server can easily do
    one and not the other, and one combined answer could not say which.
    """

    can_serve: bool
    reason: str | None = Field(
        None,
        description=(
            "What blocks publishing, shown verbatim. None when it is possible."
        ),
    )


class TrainingRunResponse(BaseModel):
    """One fine-tuning run.

    `log` is deliberately part of this shape rather than a separate endpoint:
    the client polls a run precisely because something is happening to it, and
    the output is what makes a failure legible. It is capped server-side.
    """

    id: uuid.UUID
    status: str = Field(
        ...,
        description=(
            "queued | building | training | fetching | completed | failed. "
            "The first four are active; a client polls until it sees one of "
            "the last two."
        ),
    )
    detail: str | None = Field(
        None, description="One line of context for `status`, shown verbatim."
    )
    train_pairs: int
    holdout_pairs: int
    kernel_ref: str | None = Field(
        None, description="Kaggle kernel as 'owner/slug', once the push lands."
    )
    #: Presence, not the path — the path is a server filesystem detail, and the
    #: client only needs to know whether the download button has anything to
    #: fetch. `GET /training/runs/{id}/model` resolves it.
    has_model: bool = False

    #: How far publishing this run's adapter to the local model runtime got.
    #: None means never attempted, which is a different thing from failed and
    #: is why this is not defaulted to a string.
    serve_status: str | None = Field(
        None, description="publishing | served | failed, or null if never tried."
    )
    #: One line for `serve_status`, shown verbatim. On failure it names the
    #: command to run or the tool to install.
    serve_detail: str | None = None
    #: The name the adapter is registered under in the runtime, once served.
    served_model: str | None = None
    log: str = ""
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TrainingReadiness(BaseModel):
    """Whether this server can start a run, and what stops it if not.

    Asked before the button is shown rather than discovered by pressing it: the
    two blockers (no training/ directory, no Kaggle credentials) are both
    operator configuration a user cannot fix from the UI, so an explanation
    beats a failed run.
    """

    can_train: bool
    reason: str | None = Field(
        None, description="Why a run cannot start. None when `can_train`."
    )
    active_run_id: uuid.UUID | None = Field(
        None, description="The run already in progress, if any."
    )
    min_datasets: int = Field(
        ...,
        description=(
            "Uploaded files needed before a run can produce anything. Sent "
            "rather than assumed by the client because the number follows from "
            "how the builder splits train/holdout, which is a server concern."
        ),
    )
