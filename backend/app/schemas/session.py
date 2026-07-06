"""Session schemas for API responses."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SessionResponse(BaseModel):
    """API response schema for a single session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    jira_ticket_id: str
    created_at: datetime
    bdd_status: Literal["none", "generated", "uploaded"] = "none"


class SessionBDDResponse(BaseModel):
    """API response schema for a session's most recent BDD content."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    # JSON string for source="generated"; raw Gherkin for source="uploaded"
    content: str
    source: str  # "generated" | "uploaded"
    created_at: datetime


class StoredVerificationResult(BaseModel):
    """API response schema for a stored verification result row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: str
    scenario_id: uuid.UUID
    scenario_title: str
    status: str  # "pass" | "fail"
    justification: str
    code_reference: dict
    github_links: list
    implementation_suggestion: str | None
    created_at: datetime
