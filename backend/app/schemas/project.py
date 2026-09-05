"""Project schemas.

The rule this module exists to enforce: **no credential is ever serialized
back to a client.** A stored token is write-only from the API's point of view —
it goes in, it is used server-side, and the only thing that comes back out is
whether one is set. Anything else turns a browser session into a way to exfil
every integration secret the project holds.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _require_named(value: str | None) -> str | None:
    """Reject a name that is only whitespace.

    `min_length=1` runs against the RAW string, so "   " satisfies it and then
    strips to "" at the point of storage — a project with no name in the
    switcher, created through validation that claimed to prevent exactly that.
    Stripping here makes the validated value and the stored value the same one.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("Name cannot be blank.")
    return stripped


class ProjectConfigUpdate(BaseModel):
    """Editable project settings.

    Every field is optional so a client can PATCH one section without resending
    the rest — a form that only edits Jira should not have to round-trip the
    GitHub token to leave it alone.

    Credential fields follow a three-state convention that a plain optional
    string cannot express on its own:
      - omitted / None -> leave the stored credential untouched
      - "" (empty)     -> clear the stored credential
      - any other text -> replace it
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)

    _strip_name = field_validator("name")(_require_named)

    jira_base_url: str | None = None
    jira_user_email: str | None = None
    jira_api_token: str | None = Field(
        default=None,
        description="Write-only. Omit to keep, empty string to clear.",
    )

    confluence_base_url: str | None = None
    confluence_user_email: str | None = None
    confluence_api_token: str | None = Field(
        default=None,
        description="Write-only. Omit to keep, empty string to clear.",
    )

    github_repo: str | None = None
    github_access_token: str | None = Field(
        default=None,
        description="Write-only. Omit to keep, empty string to clear.",
    )

    llm_provider: str | None = Field(
        default=None,
        description=(
            "Default provider for this project's actions. The API key is NOT "
            "stored per project — it stays system configuration."
        ),
    )
    llm_model: str | None = Field(
        default=None, description="Default model, paired with llm_provider."
    )

    embedding_provider: str | None = Field(
        default=None,
        description=(
            "Which vendor embeds this project's knowledge base: 'openai' or "
            "'voyage'. Empty falls back to EMBEDDING_PROVIDER. Changing it "
            "requires a new index at the matching dimension and a re-ingest — "
            "vectors from one model are not comparable to another's."
        ),
    )
    pinecone_index_name: str | None = Field(
        default=None,
        description=(
            "Index this project's vectors live in. Empty falls back to "
            "PINECONE_INDEX_NAME. Must have been created at the dimension the "
            "chosen embedding_provider emits (openai 1536, voyage 1024)."
        ),
    )


class ProjectCreate(BaseModel):
    """A new project. Only the name is required; configure it afterwards."""

    name: str = Field(..., min_length=1, max_length=120)

    _strip_name = field_validator("name")(_require_named)


class ProjectResponse(BaseModel):
    """A project as the client sees it — settings yes, secrets never.

    The `has_*_token` booleans exist so the UI can show "configured" without
    the value: a masked string would still have to be sent to be masked.
    """

    id: uuid.UUID
    name: str

    jira_base_url: str
    jira_user_email: str
    has_jira_token: bool

    confluence_base_url: str
    confluence_user_email: str
    has_confluence_token: bool

    github_repo: str
    has_github_token: bool

    llm_provider: str
    llm_model: str

    embedding_provider: str
    pinecone_index_name: str

    created_at: datetime
    updated_at: datetime


class ProjectSummary(BaseModel):
    """A row in the project switcher — just enough to pick one."""

    id: uuid.UUID
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}
