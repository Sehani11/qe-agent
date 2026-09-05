"""Pydantic schemas for knowledge base ingestion."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class ConfluenceIngestRequest(BaseModel):
    """Request body for POST /api/v1/knowledge/ingest/confluence.

    Three ways in, checked in this order by the service: ``page_refs`` (page
    URLs and/or numeric ids), ``space_key`` (whole space), then ``page_id``.
    ``page_id`` predates ``page_refs`` and is kept so existing callers keep
    working; new callers should use ``page_refs``, which also accepts a bare id.
    """

    space_key: str | None = None
    page_id: str | None = None
    page_refs: list[str] | None = None
    project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Whose credentials to ingest with. Omit to fall back to the "
            "server environment."
        ),
    )

    @field_validator("page_refs")
    @classmethod
    def drop_blank_refs(cls, v: list[str] | None) -> list[str] | None:
        """Blank lines are how a pasted list ends; they are not an error."""
        if v is None:
            return None
        cleaned = [ref.strip() for ref in v if ref and ref.strip()]
        return cleaned or None


class JiraIngestRequest(BaseModel):
    """Request body for POST /api/v1/knowledge/ingest/jira.

    Either a whole project (``project_key``, optionally filtered by ``sprint`` /
    ``label``) or an explicit list of tickets (``ticket_refs`` — issue URLs
    and/or bare keys). Exactly one is required; a body carrying neither is a 422,
    which is the behaviour callers had when ``project_key`` was mandatory.
    """

    project_key: str | None = None
    sprint: str | None = None
    label: str | None = None
    ticket_refs: list[str] | None = None
    project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Whose credentials to ingest with. Omit to fall back to the "
            "server environment."
        ),
    )

    @field_validator("project_key")
    @classmethod
    def validate_project_key(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,9}", v.strip().upper()):
            raise ValueError(
                "project_key must be 1-10 uppercase alphanumeric characters "
                "(e.g. PROJ, MY_PROJECT)."
            )
        return v.strip().upper()

    @field_validator("ticket_refs")
    @classmethod
    def drop_blank_refs(cls, v: list[str] | None) -> list[str] | None:
        """Blank lines are how a pasted list ends; they are not an error."""
        if v is None:
            return None
        cleaned = [ref.strip() for ref in v if ref and ref.strip()]
        return cleaned or None

    @model_validator(mode="after")
    def require_a_source(self) -> "JiraIngestRequest":
        if not self.project_key and not self.ticket_refs:
            raise ValueError("Provide project_key or ticket_refs.")
        return self


class DocumentIngestResponse(BaseModel):
    """Response body for POST /api/v1/knowledge/ingest/document (Story 4.6)."""

    ingested_count: int
    chunk_count: int
    title: str


class CodeIndexRequest(BaseModel):
    """Request body for POST /api/v1/knowledge/index/code."""

    project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Whose GitHub token to read with, and whose namespace to index "
            "into. Omit to fall back to the server environment."
        ),
    )
    repo_url: str = Field(
        ...,
        description="Repository URL or owner/repo to index.",
    )
    ref: str = Field(
        default="HEAD",
        description=(
            "Branch, tag or commit to index. Resolved to a concrete SHA before "
            "indexing, so 'indexed at' names a commit rather than a moving "
            "branch."
        ),
    )


class CodeIndexStatusResponse(BaseModel):
    """Response body for GET /api/v1/knowledge/index/code.

    ``indexed`` is false when the project has never been indexed. The frontend
    uses it to decide whether the verification form's code-index toggle can be
    enabled at all.
    """

    indexed: bool
    repo: str | None = None
    indexed_ref: str | None = None
    file_count: int = 0
    indexed_at: datetime | None = None


class KnowledgeSourceResponse(BaseModel):
    """Response schema for a persisted knowledge source record."""

    id: uuid.UUID
    user_id: str
    source_type: str
    source_url: str | None
    title: str | None
    page_count: int
    ingestion_status: str
    created_at: datetime

    model_config = {"from_attributes": True}
