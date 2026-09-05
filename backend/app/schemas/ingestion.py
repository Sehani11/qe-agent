"""Ingestion API schemas."""

import uuid

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    """Request payload to ingest a Jira ticket."""

    ticket_id_or_url: str = Field(
        ...,
        description="The Jira ticket URL (e.g. 'https://domain.atlassian.net/browse/PROJ-123') or ticket ID (e.g. 'PROJ-123').",
        min_length=1,
    )
    project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The project this session belongs to. Its Jira credentials are "
            "used; omit to fall back to the server environment."
        ),
    )


class IngestResponse(BaseModel):
    """Response payload returned after Jira ingestion completes."""

    session_id: str
    jira_ticket_id: str
    # Echoes back the submitted URL / key so the client can keep the ticket
    # field populated instead of clearing it once ingestion succeeds.
    jira_ticket_url: str
    acceptance_criteria: str
    status: str = "ready_for_bdd"
