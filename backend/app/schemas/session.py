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
    # The URL / key the user submitted at ingestion. None for sessions created
    # before it was recorded and for those created by the BDD upload path.
    jira_ticket_url: str | None = None
    created_at: datetime
    # Mirrors bdd_files.source, so every value that table can hold must be
    # listed here: POST /bdd/save writes source="edited", which used to 500
    # this response for any session whose newest BDD row was an edit.
    bdd_status: Literal["none", "generated", "uploaded", "edited"] = "none"
    # Whether this session has any verification verdicts yet.
    #
    # A SEPARATE field rather than another `bdd_status` value: the two describe
    # different things, and a session that has been verified still has a BDD in
    # some state. Folding "verified" into bdd_status would overwrite that — and
    # would quietly change what `bdd_status != "none"` means for the callers
    # using it to decide whether a BDD exists at all.
    verification_status: Literal["none", "completed"] = "none"


class SessionListResponse(BaseModel):
    """One page of the current user's sessions, newest first.

    `total` is the count of all the user's sessions, not the page size, so the
    client can show "x-y of n" and disable paging controls at the ends without
    fetching another page to discover it is empty.
    """

    items: list[SessionResponse]
    total: int
    limit: int
    offset: int


class SessionBDDResponse(BaseModel):
    """API response schema for a session's most recent BDD content."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    # JSON string for source="generated"; raw Gherkin for "uploaded"/"edited"
    content: str
    source: str  # "generated" | "uploaded" | "edited"
    created_at: datetime


class StoredVerificationResult(BaseModel):
    """API response schema for a stored verification result row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: str
    scenario_id: uuid.UUID
    scenario_title: str
    status: str  # "pass" | "partial" | "fail" | "inconclusive"
    justification: str
    code_reference: dict
    github_links: list
    implementation_suggestion: str | None
    # Story 4.4: expose persisted RAG context so it renders on session revisit.
    # Auto-populated from the ORM `rag_context` JSONB column via from_attributes.
    rag_context: list[dict] | None = None
    # The source this verdict was checked against, so a revisited session can
    # both show it and restore the workspace's source field. None for rows
    # written before these columns existed.
    verification_mode: str | None = None
    github_input: str | None = None
    created_at: datetime
