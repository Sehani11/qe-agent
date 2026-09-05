"""BDD generation schemas for API requests and responses."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BDDGenerateRequest(BaseModel):
    """Request payload for BDD scenario generation.

    The two LLM-selection fields are declared inline instead of inheriting
    `app.schemas.llm.LLMSelectionMixin`, which is what every other LLM-backed
    request does. This module MUST import nothing beyond stdlib and pydantic:
    `training/serve/app.py` loads it by file path, with no package context, to
    get `BDDGenerateResponse` without colliding with its own top-level module
    named `app` — a cross-module import here breaks the serving shim at
    startup. `test_bdd_request_matches_the_shared_llm_selection` pins the two
    declarations together so they cannot drift.
    """

    session_id: UUID = Field(..., description="Unique ID for the session")
    acceptance_criteria: str = Field(
        ..., description="The acceptance criteria to convert into BDD format"
    )
    llm_provider: str | None = Field(
        default=None,
        description=(
            "LLM provider for this request: 'openai', 'claude', or "
            "'local'/'ollama'. Omit to use the server default."
        ),
    )
    llm_model: str | None = Field(
        default=None,
        description=(
            "Model identifier for this request, e.g. 'gpt-4o'. Must belong to "
            "llm_provider. Omit to use that provider's configured default."
        ),
    )
    bdd_model_provider: str | None = Field(
        default=None,
        description=(
            "Which model generates the scenarios: 'fine_tuned' for the "
            "domain-specific model, or 'general_llm' to use llm_provider/"
            "llm_model. Omit to use the server default (BDD_MODEL_PROVIDER)."
        ),
    )
    training_opt_in: bool | None = Field(
        default=None,
        description=(
            "Whether this content may be used to fine-tune a model. Can only "
            "narrow the deployment's TRAINING_DATA_OPT_IN policy, never widen "
            "it. Omit to accept that policy."
        ),
    )


class BDDScenario(BaseModel):
    """A BDD scenario derived from acceptance criteria."""

    source_ac_clause: str = Field(
        ..., description="The specific AC clause that this scenario covers."
    )
    feature: str = Field(..., description="The feature name or grouping.")
    scenario: str = Field(..., description="The scenario title.")
    given: str = Field(..., description="The Given step text.")
    when: str = Field(..., description="The When step text.")
    then: str = Field(..., description="The Then step text.")


class BDDGenerateResponse(BaseModel):
    """List of generated BDD scenarios."""

    scenarios: list[BDDScenario] = Field(
        default_factory=list, description="The list of generated BDD scenarios"
    )


class BDDCoverage(BaseModel):
    """How much of the submitted criteria the generated scenarios actually cite.

    Measured against the RAW criteria text, not against what reached the model,
    so a clause lost on the way in still counts against coverage.
    """

    total_clauses: int = Field(
        ..., description="Numbered clauses parsed from the submitted criteria"
    )
    covered_clauses: int = Field(
        ..., description="Clauses cited by at least one generated scenario"
    )
    uncovered: list[str] = Field(
        default_factory=list,
        description="Labels of clauses no scenario cited, in ticket order",
    )
    ratio: float | None = Field(
        default=None,
        description=(
            "covered/total, or null when no numbered clauses could be parsed "
            "— undefined coverage, which is not the same as zero"
        ),
    )


class BDDGenerateResult(BDDGenerateResponse):
    """What /generate returns: the scenarios plus a coverage read-out.

    A separate model from `BDDGenerateResponse` on purpose. That class's JSON
    schema is handed to the model as `response_format`, so any field added to
    it becomes a field the LLM is asked to populate — coverage is measured
    server-side from the model's own output and must never be self-reported.
    """

    coverage: BDDCoverage | None = Field(
        default=None,
        description="Clause coverage of the generated scenarios; null if unmeasured",
    )


class BDDSaveRequest(BaseModel):
    """Request payload for saving edited BDD content (Story 6.4).

    Deliberately carries no parent id. The originating generated row is
    resolved server-side, because exposing it would mean adding a field to
    BDDGenerateResponse — whose JSON schema is handed to the model as
    `response_format`, so the LLM would be asked to populate it.
    """

    session_id: UUID = Field(..., description="The session this edit belongs to")
    content: str = Field(..., description="The edited Gherkin content, verbatim")
    training_opt_in: bool | None = Field(
        default=None,
        description=(
            "Whether this content may be used to fine-tune a model. Can only "
            "narrow the deployment's TRAINING_DATA_OPT_IN policy, never widen "
            "it. Omit to accept that policy."
        ),
    )


class BDDSaveResponse(BaseModel):
    """Response from saving edited BDD content."""

    id: UUID = Field(..., description="The newly created bdd_files row")
    session_id: str = Field(..., description="The session this edit belongs to")
    source: str = Field(..., description="Always 'edited' for this endpoint")
    created_at: datetime = Field(..., description="When the edit was saved")


class BDDUploadResponse(BaseModel):
    """Response from uploading a .feature file."""

    session_id: str = Field(..., description="The session this file belongs to")
    content: str = Field(..., description="The uploaded feature file content")
    jira_ticket_id: str = Field(
        ...,
        description=(
            "The session's jira_ticket_id. For sessions created on-demand "
            "by a manual upload, this is a placeholder derived from the filename."
        ),
    )


class FineTunedStatus(BaseModel):
    """Whether the fine-tuned model behind the toggle is actually there.

    Read from the serving shim on each request rather than cached: the model
    can go at any time — a wipe removes it, and the shim is a process someone
    starts and stops by hand — so a remembered "available" would be the exact
    thing this exists to prevent.
    """

    available: bool = Field(
        ..., description="True when a fine-tuned model is being served right now."
    )
    model: str | None = Field(
        None, description="Name of the served model, when there is one."
    )
    detail: str = Field(
        ..., description="Why it is unavailable, or what is being served."
    )
