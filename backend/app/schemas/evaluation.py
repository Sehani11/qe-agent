"""Schemas for the side-by-side model comparison endpoint.

The comparison the CLI (`app.evaluate_models`) performs over a whole set, done
for a single input and returned synchronously so the UI can show it.
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator


class CompareRequest(BaseModel):
    """One input to send to both models.

    Exactly one of the two fields is required. `acceptance_criteria` wins when
    both are supplied, so a caller that pastes text is never surprised by a
    stale ticket id also sitting in the form.
    """

    jira_ticket_id: str | None = Field(
        default=None, description="Ticket id or URL; its criteria are fetched"
    )
    acceptance_criteria: str | None = Field(
        default=None, description="Criteria text, used as-is"
    )
    #: Which project's Jira to fetch the ticket from. Without it the
    #: fetch falls back to the server environment, which reports "Jira is
    #: not configured" for anyone whose credentials live in project
    #: settings. Omit only when supplying criteria text directly.
    project_id: uuid.UUID | None = Field(
        default=None, description="Project whose Jira credentials to use"
    )

    @model_validator(mode="after")
    def _one_input_required(self) -> "CompareRequest":
        if not (self.jira_ticket_id or "").strip() and not (
            self.acceptance_criteria or ""
        ).strip():
            raise ValueError(
                "Provide either jira_ticket_id or acceptance_criteria."
            )
        return self


class ProviderResult(BaseModel):
    """What one model produced, and how it scored."""

    configured_provider: str
    #: Which model ANSWERED. When this differs from `configured_provider` the
    #: fine-tuned endpoint failed and the general LLM answered instead. The UI
    #: must show this: the output looks identical either way, so a comparison
    #: that silently ran the same model twice is otherwise indistinguishable
    #: from a real one.
    effective_provider: str | None = None
    fallback_reason: str | None = None
    model_identifier: str = ""

    succeeded: bool
    error: str | None = None
    scenarios: list[dict[str, Any]] = Field(default_factory=list)

    latency_seconds: float | None = None
    #: Fraction of AC clauses with at least one scenario citing them. `None`
    #: when no clauses could be parsed — never 0.0, which would read as "the
    #: model covered nothing" rather than "this input has no numbered clauses".
    coverage: float | None = None
    #: Fraction of scenarios that near-duplicate an earlier one. Read together
    #: with coverage: a model can reach full coverage by repeating itself.
    duplicate_rate: float | None = None
    scenario_count: int = 0

    @property
    def is_trustworthy(self) -> bool:
        return (
            self.succeeded
            and self.effective_provider is not None
            and self.effective_provider == self.configured_provider
        )


class CompareResponse(BaseModel):
    """Both models' results for the same input."""

    acceptance_criteria: str
    jira_ticket_id: str | None = None
    ac_clauses: list[str] = Field(default_factory=list)
    results: list[ProviderResult]
    #: True when every provider answered as configured. False means one side
    #: did not answer, so there is nothing to compare it against — the missing
    #: column is left empty rather than filled with the other model's output.
    comparable: bool = True
    notes: list[str] = Field(default_factory=list)


# --- Batch runs, saved to the database ------------------------------------


class BatchCompareRequest(BaseModel):
    """Several tickets compared in one go, saved under one run id."""

    jira_ticket_ids: list[str] = Field(
        default_factory=list, max_length=25, description="Ticket ids or URLs"
    )
    #: Groups these rows so they can be reported and deleted together. Reusing
    #: an existing id ADDS to that run — the table upserts on
    #: (run_id, item_id, configured_provider), so re-running one ticket
    #: replaces its rows rather than duplicating them.
    run_id: str = Field(min_length=1, max_length=64)
    #: Which project's Jira to fetch the ticket from. Without it the
    #: fetch falls back to the server environment, which reports "Jira is
    #: not configured" for anyone whose credentials live in project
    #: settings. Omit only when supplying criteria text directly.
    project_id: uuid.UUID | None = Field(
        default=None, description="Project whose Jira credentials to use"
    )

    @model_validator(mode="after")
    def _at_least_one_ticket(self) -> "BatchCompareRequest":
        cleaned = [t.strip() for t in self.jira_ticket_ids if t.strip()]
        if not cleaned:
            raise ValueError("Provide at least one Jira ticket id.")
        self.jira_ticket_ids = cleaned
        return self


class BatchItemOutcome(BaseModel):
    """What happened for one ticket in a batch."""

    jira_ticket_id: str
    saved: bool
    comparable: bool = False
    ac_clause_count: int = 0
    error: str | None = None


class BatchCompareResponse(BaseModel):
    run_id: str
    requested: int
    saved: int
    items: list[BatchItemOutcome]


class RunSummary(BaseModel):
    """One saved run, for the list view."""

    run_id: str
    item_count: int
    row_count: int
    #: Rows configured `fine_tuned` that were actually answered by something
    #: else. A run where this equals the fine-tuned row count compares the
    #: general LLM with itself.
    degraded_rows: int
    first_created_at: str | None = None
    last_created_at: str | None = None


class ProviderMetrics(BaseModel):
    provider: str
    items_scored: int
    degraded_excluded: int
    success_rate: float | None = None
    coverage: float | None = None
    duplicate_rate: float | None = None
    reference_alignment: float | None = None
    latency_seconds: float | None = None


class GroupMetrics(BaseModel):
    #: `on_domain` and `off_domain` are reported separately and never averaged
    #: together: only the off-domain group carries reference scenarios, so a
    #: combined number would describe neither group.
    domain_group: str
    providers: list[ProviderMetrics]


class RunReport(BaseModel):
    """Aggregated metrics for one run, or for every run combined."""

    run_ids: list[str]
    item_count: int
    row_count: int
    groups: list[GroupMetrics]
    notes: list[str] = Field(default_factory=list)
