"""Verification schemas for API requests and responses."""

from typing import Literal

from pydantic import BaseModel, Field


class VerificationFetchRequest(BaseModel):
    """Request payload for GitHub code fetching."""

    session_id: str = Field(..., description="Unique ID for the session")
    mode: Literal["exact_files", "full_repo", "pull_request"] = Field(
        ..., description="GitHub source selection mode"
    )
    github_input: str = Field(
        ...,
        description="File URLs (one per line), repo URL, or PR URL depending on mode",
    )


class FetchedFile(BaseModel):
    """A single file fetched from GitHub."""

    path: str = Field(..., description="File path relative to the repository root")
    content: str = Field(..., description="Raw file content or diff patch")
    github_url: str | None = Field(
        default=None,
        description="Absolute GitHub blob URL for this file (used for UI links)",
    )


class VerificationFetchResponse(BaseModel):
    """Response containing all files fetched from GitHub."""

    session_id: str = Field(..., description="Session ID from the request")
    mode: str = Field(..., description="Fetch mode used")
    fetched_files: list[FetchedFile] = Field(
        default_factory=list,
        description="List of fetched files with their content",
    )


# ---------------------------------------------------------------------------
# Story 2.3: LLM Verification schemas
# ---------------------------------------------------------------------------


class CodeReference(BaseModel):
    """Points to the specific code location used as evidence in a verdict."""

    file: str | None = Field(None, description="File path in the repository")
    function: str | None = Field(None, description="Function or method name")
    line: int | None = Field(None, description="Line number")


class VerificationRunRequest(BaseModel):
    """Request payload for the LLM verification run."""

    session_id: str = Field(..., description="Unique ID for the session")
    bdd_content: str = Field(..., description="Gherkin BDD scenarios to evaluate")
    fetched_files: list[FetchedFile] = Field(
        default_factory=list,
        description="Source files fetched from GitHub for evaluation",
    )


class VerificationVerdict(BaseModel):
    """Per-scenario verdict returned by the LLM."""

    scenario_id: str = Field(..., description="UUID of the scenario (same as input)")
    scenario_title: str = Field(
        ..., description="Title of the scenario (same as input)"
    )
    status: Literal["pass", "fail"] = Field(
        ..., description="Whether the scenario is fully covered by the code"
    )
    justification: str = Field(
        ..., description="Natural-language explanation referencing specific code"
    )
    code_reference: CodeReference = Field(
        ..., description="Specific code location used as evidence"
    )
    github_links: list[str] = Field(
        default_factory=list,
        description="Repository file paths or URLs used as evidence",
    )
    implementation_suggestion: str | None = Field(
        default=None,
        description=(
            "Actionable guidance for fixing the gap (non-null only when status=fail)"
        ),
    )


class VerificationCompleteEvent(BaseModel):
    """Final summary event emitted after all scenarios are processed."""

    type: Literal["complete"] = "complete"
    total: int = Field(..., description="Total number of scenarios evaluated")
    passed: int = Field(..., description="Number of scenarios that passed")
    failed: int = Field(..., description="Number of scenarios that failed")


# ---------------------------------------------------------------------------
# Story 2.5: Agentic verification schema
# ---------------------------------------------------------------------------


class AgenticVerificationRequest(BaseModel):
    """Request payload for the agentic LLM verification run (Story 2.5)."""

    session_id: str = Field(..., description="Unique ID for the session")
    bdd_content: str = Field(..., description="Gherkin BDD scenarios to evaluate")
    mode: Literal["exact_files", "full_repo", "pull_request"] = Field(
        ..., description="GitHub source selection mode"
    )
    github_input: str = Field(
        ...,
        description="File URLs (one per line), repo URL, or PR URL depending on mode",
    )
