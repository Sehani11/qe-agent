"""Project model — the container sessions now belong to.

A project holds the settings a team keeps re-entering: which Jira and
Confluence live behind this work, which repository verification reads, and
which model to default to. Actions prepopulate from here and stay overridable,
so the project is a starting point rather than a lock.

Credentials are stored ENCRYPTED, not hashed — see `app.core.crypto` for why
the distinction is load-bearing. Nothing in this module decrypts; that happens
at the point of use, so a row can be read for display without touching secrets.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Project(Base):
    """One project: its integration settings and encrypted credentials."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    # --- Jira -------------------------------------------------------------
    jira_base_url: Mapped[str] = mapped_column(String, nullable=False, default="")
    jira_user_email: Mapped[str] = mapped_column(String, nullable=False, default="")
    #: Fernet ciphertext. Empty means "not configured" — the encryption helper
    #: never encrypts an empty string, so this stays unambiguous.
    jira_api_token_encrypted: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )

    # --- Confluence -------------------------------------------------------
    confluence_base_url: Mapped[str] = mapped_column(
        String, nullable=False, default=""
    )
    confluence_user_email: Mapped[str] = mapped_column(
        String, nullable=False, default=""
    )
    confluence_api_token_encrypted: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )

    # --- GitHub -----------------------------------------------------------
    #: Default repository for verification, as "owner/repo" or a full URL. The
    #: verification form prepopulates from it and the user can point elsewhere.
    github_repo: Mapped[str] = mapped_column(String, nullable=False, default="")
    github_access_token_encrypted: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )

    # --- Model defaults ---------------------------------------------------
    # The CHOICE lives here; the API keys do not. Keys are system configuration
    # (OPENAI_API_KEY / ANTHROPIC_API_KEY) because they are billing credentials
    # for the deployment, not per-project integration secrets — and putting
    # them here would mean every project could spend against a different
    # account with no operator visibility.
    #
    # Empty means "no project preference": the client falls back to its own
    # default rather than being forced onto one.
    llm_provider: Mapped[str] = mapped_column(String, nullable=False, default="")
    llm_model: Mapped[str] = mapped_column(String, nullable=False, default="")

    # --- Knowledge base ---------------------------------------------------
    # Which vendor embeds this project's text, and the index those vectors go
    # in. One setting in two halves: an index is built with exactly ONE
    # embedding model, and a query is only comparable to vectors produced by
    # that same model. Changing either without the other is why Pinecone
    # answers "Vector dimension 1024 does not match the dimension of the index
    # 1536" — a loud failure, and the good case. The quiet one would be an
    # index of the right width built by the wrong model.
    #
    # Per PROJECT rather than per request: a project's write path and read path
    # both resolve from this row, so the two always agree. Per request they
    # could not — a ticket ingested under one provider would be invisible to a
    # question asked under another, silently.
    #
    # As with the model above, the choice lives here and the key does not:
    # VOYAGE_API_KEY is deployment configuration for the same billing reason.
    # Empty means "no project preference" — fall back to the environment.
    embedding_provider: Mapped[str] = mapped_column(
        String, nullable=False, default=""
    )
    pinecone_index_name: Mapped[str] = mapped_column(
        String, nullable=False, default=""
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
