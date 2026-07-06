"""Ingestion API schemas."""

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    """Request payload to ingest a Jira ticket."""

    ticket_id_or_url: str = Field(
        ...,
        description="The Jira ticket URL (e.g. 'https://domain.atlassian.net/browse/PROJ-123') or ticket ID (e.g. 'PROJ-123').",
        min_length=1,
    )


class IngestResponse(BaseModel):
    """Response payload returned after Jira ingestion completes."""

    session_id: str
    jira_ticket_id: str
    acceptance_criteria: str
    status: str = "ready_for_bdd"
