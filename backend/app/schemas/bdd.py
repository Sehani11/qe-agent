"""BDD generation schemas for API requests and responses."""

from uuid import UUID

from pydantic import BaseModel, Field


class BDDGenerateRequest(BaseModel):
    """Request payload for BDD scenario generation."""

    session_id: UUID = Field(..., description="Unique ID for the session")
    acceptance_criteria: str = Field(
        ..., description="The acceptance criteria to convert into BDD format"
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
