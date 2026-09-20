"""Verification schemas for API requests and responses."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.llm import LLMSelectionMixin


class FetchedFile(BaseModel):
    """A single file fetched from GitHub."""

    path: str = Field(..., description="File path relative to the repository root")
    content: str = Field(..., description="Raw file content or diff patch")
    github_url: str | None = Field(
        default=None,
        description="Absolute GitHub blob URL for this file (used for UI links)",
    )


# ---------------------------------------------------------------------------
# Story 2.3: LLM Verification schemas
# ---------------------------------------------------------------------------


class CodeReference(BaseModel):
    """Points to the specific code location used as evidence in a verdict."""

    file: str | None = Field(None, description="File path in the repository")
    function: str | None = Field(None, description="Function or method name")
    line: int | None = Field(None, description="Line number")


class RagContextItem(BaseModel):
    """A single retrieved knowledge chunk attached to a verification verdict."""

    source: str = Field(
        ..., description="Knowledge source type: 'confluence' or 'jira'"
    )
    source_id: str = Field(
        ..., description="Confluence page ID or Jira ticket key"
    )
    # Deliberately an excerpt, not the whole chunk. Carrying the full text was
    # tried and reverted: chunks are ~500-word windows cut on sentence count,
    # so one opens mid-table and ends mid-thought however well it is rendered,
    # and flattened Confluence tables read as a column of stray words. The
    # panel is for recognising which source backed a verdict; `url` is how a
    # reader gets the document itself, properly laid out.
    snippet: str = Field(
        ...,
        description=(
            "Excerpt of the retrieved chunk, at most 300 characters, cut on a "
            "word boundary and ellipsised when the chunk runs longer"
        ),
    )
    # Story 4.4 — optional with defaults so persisted rag_context from Story 4.3
    # (which lacks these keys) still validates.
    title: str = Field(
        default="",
        description="Human-readable source title (Confluence page / Jira summary)",
    )
    url: str = Field(
        default="",
        description="Deep link to the source; empty when not linkable",
    )


class VerificationVerdict(BaseModel):
    """Per-scenario verdict returned by the LLM."""

    scenario_id: str = Field(..., description="UUID of the scenario (same as input)")
    scenario_title: str = Field(
        ..., description="Title of the scenario (same as input)"
    )
    status: Literal["pass", "partial", "fail", "inconclusive"] = Field(
        ...,
        description=(
            "Whether the scenario is fully covered by the code. 'partial' "
            "means the behaviour exists but is not reachable the way this "
            "scenario describes — typically wired to a different trigger, or "
            "implemented on one side of the stack only. 'inconclusive' means "
            "the run could not gather evidence either way — distinct from "
            "'fail', which asserts the behaviour is genuinely absent"
        ),
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
            "Actionable guidance (non-null when status=fail, when "
            "status=partial to say what is missing to close the gap, or when "
            "status=inconclusive to say what would settle it; null when "
            "status=pass)"
        ),
    )
    rag_context: list[RagContextItem] | None = Field(
        default=None,
        description="Knowledge chunks retrieved from the user's knowledge base",
    )


class VerificationCompleteEvent(BaseModel):
    """Final summary event emitted after all scenarios are processed."""

    type: Literal["complete"] = "complete"
    total: int = Field(..., description="Total number of scenarios evaluated")
    passed: int = Field(..., description="Number of scenarios that passed")
    failed: int = Field(..., description="Number of scenarios that failed")
    # Defaulted so a summary persisted or produced before these fields existed
    # still validates; total is passed + partial + failed + inconclusive.
    inconclusive: int = Field(
        default=0,
        description="Number of scenarios that could not be decided either way",
    )
    partial: int = Field(
        default=0,
        description=(
            "Number of scenarios whose behaviour exists but is not reachable "
            "as the scenario describes"
        ),
    )


# ---------------------------------------------------------------------------
# Story 2.5: Agentic verification schema
# ---------------------------------------------------------------------------


class AgenticVerificationRequest(LLMSelectionMixin):
    """Request payload for the agentic LLM verification run (Story 2.5)."""

    session_id: str = Field(..., description="Unique ID for the session")
    project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Whose GitHub token to read the repository with. Omit to fall "
            "back to the server environment."
        ),
    )
    bdd_content: str = Field(..., description="Gherkin BDD scenarios to evaluate")
    mode: Literal["exact_files", "full_repo", "pull_request"] = Field(
        ..., description="GitHub source selection mode"
    )
    github_input: str = Field(
        ...,
        description="File URLs (one per line), repo URL, or PR URL depending on mode",
    )
    use_knowledge_base: bool = Field(
        default=False,
        description="Enrich each scenario's prompt with project knowledge-base "
        "context (opt-in per run; default off).",
    )
    code_index_enabled: bool = Field(
        default=False,
        description="Use the project's code index to suggest which files each "
        "scenario's implementation is likely in (opt-in per run; default off). "
        "Suggestions are a starting point for the agent's own reads, never "
        "evidence for a verdict. Ignored when the project has no index or its "
        "index is stale.",
    )
