"""Resolve the integration credentials a request should run with.

A project supplies its own Jira / Confluence / GitHub credentials; the
environment supplies the deployment-wide fallback. This module is the single
place that decision is made, so no service has to know that projects exist.

The rule that matters is **all-or-nothing per integration**. A project either
supplies its whole credential set or none of it — the resolver never mixes a
project's base URL with the environment's token, or vice versa. Field-by-field
fallback looks tidier and is dangerous: it would happily send one Atlassian
instance's token to a different instance's URL, which is either an
authentication failure or, with two projects on one tenant, a credential
crossing a boundary it was never meant to cross.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import CredentialEncryptionError, decrypt
from app.models.project import Project


@dataclass(frozen=True)
class JiraCredentials:
    """Everything a Jira call needs, from one source."""

    base_url: str
    user_email: str
    api_token: str
    #: Which source answered — "project" or "environment". Carried for error
    #: messages: "no Jira token configured" is unactionable without knowing
    #: which of the two places the user should go and fix.
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.api_token and self.user_email)


@dataclass(frozen=True)
class ConfluenceCredentials:
    base_url: str
    user_email: str
    api_token: str
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_token)


@dataclass(frozen=True)
class GitHubCredentials:
    access_token: str
    #: The project's default repository, if it set one. Empty means the caller
    #: must supply its own target.
    default_repo: str
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.access_token)


@dataclass(frozen=True)
class VectorConfig:
    """Everything one embedding + Pinecone call needs, resolved together.

    ``provider`` and ``index_name`` travel as a pair on purpose. An index holds
    vectors from exactly one embedding model, and a query is only comparable to
    vectors that model produced — so a caller must never end up with one
    project's provider and another's index. Passing a single resolved object
    makes that pairing hard to get wrong; passing two strings would not.

    Unlike the credential resolvers above, this one is NOT all-or-nothing. A
    provider and an index name are configuration rather than secrets, so there
    is no token to cross-contaminate: a project may name an index while leaving
    the vendor at the deployment default, and that combination is meaningful.
    """

    provider: str
    model: str
    api_key: str
    index_name: str
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.index_name)

    def __repr__(self) -> str:
        """Never render the key.

        A dataclass's generated repr prints every field, and this object is
        passed down through services that log and raise — one `logger.debug`
        or an unhandled traceback would put a live billing credential in a log
        file. The other fields are the useful ones for debugging anyway.
        """
        held = "set" if self.api_key else "missing"
        return (
            f"VectorConfig(provider={self.provider!r}, model={self.model!r}, "
            f"api_key=<{held}>, index_name={self.index_name!r}, "
            f"source={self.source!r})"
        )


async def load_project(
    db: AsyncSession, user_id: str, project_id: uuid.UUID | None
) -> Project | None:
    """Fetch a project the caller owns, or None.

    Scoped by user_id as well as id: a project id arriving from a request is
    never treated as proof of access, so another user's id resolves to None and
    the caller falls back to the environment rather than borrowing their
    credentials.
    """
    if project_id is None:
        return None
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def ensure_project(
    db: AsyncSession, user_id: str, project_id: uuid.UUID | None
) -> Project:
    """The project a new session belongs to — always one, never None.

    `sessions.project_id` is NOT NULL, so this cannot return None and leave the
    caller to insert a null. Resolution order:

      1. The requested project, if the caller owns it.
      2. Their oldest project — the same one the migration moved existing
         sessions into, so a client that forgets to send an id lands where its
         history already is.
      3. A freshly created "Project-1", so a brand-new account is not blocked
         from working before it has visited the projects page.

    An unowned id falls through to (2) rather than erroring: it is
    indistinguishable from a stale id left in another browser, and refusing
    would strand the user with no way forward from the session page.
    """
    project = await load_project(db, user_id, project_id)
    if project is not None:
        return project

    result = await db.execute(
        select(Project)
        .where(Project.user_id == user_id)
        .order_by(Project.created_at.asc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    created = Project(user_id=user_id, name="Project-1")
    db.add(created)
    # Flushed rather than committed: the caller is mid-transaction building a
    # session, and the project must not survive a rollback of that work.
    await db.flush()
    return created


def _project_token(ciphertext: str) -> str:
    """Decrypt a stored token, treating an unreadable one as absent.

    A credential encrypted under a rotated key cannot be used, and raising here
    would take down every request for a project with one stale field. Falling
    back to the environment keeps the app usable; the project page still shows
    the credential as configured, which is where the mismatch gets fixed.
    """
    try:
        return decrypt(ciphertext)
    except CredentialEncryptionError:
        return ""


def jira_credentials_for(project: Project | None) -> JiraCredentials:
    """The project's Jira credentials if it has a token, else the environment's."""
    if project is not None:
        token = _project_token(project.jira_api_token_encrypted)
        if token:
            return JiraCredentials(
                base_url=project.jira_base_url,
                user_email=project.jira_user_email,
                api_token=token,
                source="project",
            )

    return JiraCredentials(
        base_url=settings.jira_base_url,
        user_email=settings.jira_user_email,
        api_token=settings.jira_api_token,
        source="environment",
    )


def confluence_credentials_for(project: Project | None) -> ConfluenceCredentials:
    """The project's Confluence credentials if it has a token, else the environment's."""
    if project is not None:
        token = _project_token(project.confluence_api_token_encrypted)
        if token:
            return ConfluenceCredentials(
                base_url=project.confluence_base_url,
                user_email=project.confluence_user_email,
                api_token=token,
                source="project",
            )

    return ConfluenceCredentials(
        base_url=settings.confluence_base_url,
        user_email=settings.confluence_user_email,
        api_token=settings.confluence_api_token,
        source="environment",
    )


def github_credentials_for(project: Project | None) -> GitHubCredentials:
    """The project's GitHub token if it has one, else the environment's.

    `default_repo` is read from the project even when the token came from the
    environment: a repository name is not a credential, so there is nothing to
    cross-contaminate, and a project that names a repo without storing a token
    is a normal setup on a public repository.
    """
    default_repo = project.github_repo if project is not None else ""

    if project is not None:
        token = _project_token(project.github_access_token_encrypted)
        if token:
            return GitHubCredentials(
                access_token=token, default_repo=default_repo, source="project"
            )

    return GitHubCredentials(
        access_token=settings.github_access_token,
        default_repo=default_repo,
        source="environment",
    )


def vector_config_for(project: Project | None) -> VectorConfig:
    """The project's embedding vendor and index, each falling back to the env.

    The two halves resolve independently — see ``VectorConfig`` for why that is
    safe here when it would not be for a credential. A project that names only
    an index keeps the deployment's vendor, which is the ordinary way to give a
    project its own namespace without changing how text is embedded.

    The API key always comes from the environment: it is a billing credential
    for the deployment, and per-project keys would let each project spend
    against a different account with no operator visibility. Only the choice is
    the project's.

    ``source`` reports "project" when either half came from the project, since
    its purpose is to tell a user which of the two places to go and edit.
    """
    from app.services import vector_service as _vs

    configured_provider = ""
    configured_index = ""
    if project is not None:
        configured_provider = (project.embedding_provider or "").strip()
        configured_index = (project.pinecone_index_name or "").strip()

    provider = _vs.normalise_provider(
        configured_provider or settings.embedding_provider
    )
    index_name = configured_index or settings.pinecone_index_name

    return VectorConfig(
        provider=provider,
        model=_vs.model_for_provider(provider),
        api_key=_vs.api_key_for_provider(provider),
        index_name=index_name,
        source=(
            "project" if (configured_provider or configured_index) else "environment"
        ),
    )


def missing_credentials_message(integration: str, source: str) -> str:
    """Tell the user WHERE to add the credential that is missing."""
    if source == "project":
        return (
            f"{integration} is not fully configured for this project. "
            f"Add the base URL, user email and API token in project settings."
        )
    return (
        f"{integration} is not configured. Add credentials in project settings, "
        f"or set them in the server environment."
    )
