"""Traceability report schemas (Story 5.3).

Assembled from verification_results + bdd_files — no LLM. The rows are kept
flat and export-friendly (Story 5.4 renders them to PDF/CSV).
"""

from datetime import datetime

from pydantic import BaseModel, Field


class TraceabilityRow(BaseModel):
    """One AC clause → BDD scenario → verification verdict linkage."""

    ac_clause: str | None = Field(
        default=None,
        description="Source AC clause the scenario covers (null for uploaded BDD)",
    )
    scenario_title: str
    scenario_status: str  # "pass" | "partial" | "fail" | "inconclusive"
    justification: str
    code_reference: dict
    github_links: list
    implementation_suggestion: str | None = None
    rag_context: list[dict] | None = None


class TraceabilitySummary(BaseModel):
    """Roll-up counts for the report.

    Carries one bucket per verdict rather than pass/not-pass. Collapsing
    `inconclusive` into `failed` reported "the code does not do this" for
    scenarios the run explicitly declined to judge, which is the exact
    false negative the verification prompt is built to avoid; and collapsing
    `partial` into `failed` hides behaviour that exists but is not wired up.

    `inconclusive` and `partial` default to 0 so a report assembled from rows
    written before those verdicts existed still validates.
    """

    total: int
    passed: int
    failed: int
    partial: int = 0
    inconclusive: int = 0


class TraceabilityReport(BaseModel):
    """The full traceability report for a session."""

    session_id: str
    jira_ticket_id: str
    generated_at: datetime
    summary: TraceabilitySummary
    rows: list[TraceabilityRow]
