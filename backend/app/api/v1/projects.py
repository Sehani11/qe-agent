"""Project CRUD and configuration.

Every route is scoped to the authenticated user. A project id from the client
is never trusted as an access grant — it is looked up together with the user id,
so asking for someone else's project is indistinguishable from asking for one
that does not exist.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.crypto import CredentialEncryptionError, encrypt
from app.core.database import get_db
from app.models.project import Project
from app.schemas.project import (
    ProjectConfigUpdate,
    ProjectCreate,
    ProjectResponse,
    ProjectSummary,
)

router = APIRouter()


def _to_response(project: Project) -> ProjectResponse:
    """Map a row to the client view, converting secrets to presence flags."""
    return ProjectResponse(
        id=project.id,
        name=project.name,
        jira_base_url=project.jira_base_url,
        jira_user_email=project.jira_user_email,
        has_jira_token=bool(project.jira_api_token_encrypted),
        confluence_base_url=project.confluence_base_url,
        confluence_user_email=project.confluence_user_email,
        has_confluence_token=bool(project.confluence_api_token_encrypted),
        github_repo=project.github_repo,
        has_github_token=bool(project.github_access_token_encrypted),
        llm_provider=project.llm_provider,
        llm_model=project.llm_model,
        embedding_provider=project.embedding_provider,
        pinecone_index_name=project.pinecone_index_name,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


async def _owned_project(
    project_id: uuid.UUID, current_user: str, db: AsyncSession
) -> Project:
    """Fetch a project the caller owns, or 404.

    404 rather than 403 for someone else's project: a 403 would confirm the id
    exists, which is a membership oracle over other users' data.
    """
    result = await db.execute(
        select(Project).where(
            Project.id == project_id, Project.user_id == current_user
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> list[ProjectSummary]:
    """Every project the caller owns, oldest first so the switcher is stable."""
    result = await db.execute(
        select(Project)
        .where(Project.user_id == current_user)
        .order_by(Project.created_at.asc())
    )
    return [ProjectSummary.model_validate(p) for p in result.scalars().all()]


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    request: ProjectCreate,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> ProjectResponse:
    """Create an empty project. Configuration comes afterwards."""
    project = Project(user_id=current_user, name=request.name.strip())
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return _to_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> ProjectResponse:
    """One project's settings. Credentials come back as presence flags only."""
    return _to_response(await _owned_project(project_id, current_user, db))


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    request: ProjectConfigUpdate,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> ProjectResponse:
    """Update settings, encrypting any credential supplied.

    Uses `exclude_unset` so an omitted field means "leave it alone" rather than
    "set it to null" — a form editing one section must not blank the others.
    """
    project = await _owned_project(project_id, current_user, db)
    supplied = request.model_dump(exclude_unset=True)

    for field in (
        "name",
        "jira_base_url",
        "jira_user_email",
        "confluence_base_url",
        "confluence_user_email",
        "github_repo",
        "llm_provider",
        "llm_model",
        "embedding_provider",
        "pinecone_index_name",
    ):
        if field in supplied and supplied[field] is not None:
            value = supplied[field]
            setattr(project, field, value.strip() if isinstance(value, str) else value)

    # Credentials take the three-state path: absent keeps, "" clears, text
    # replaces. encrypt("") returns "" so clearing needs no special case.
    credential_columns = {
        "jira_api_token": "jira_api_token_encrypted",
        "confluence_api_token": "confluence_api_token_encrypted",
        "github_access_token": "github_access_token_encrypted",
    }
    try:
        for field, column in credential_columns.items():
            if field in supplied and supplied[field] is not None:
                setattr(project, column, encrypt(supplied[field].strip()))
    except CredentialEncryptionError as exc:
        # A misconfigured server key, not a bad request — but the caller needs
        # to know their credential was NOT saved rather than silently dropped.
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(project)
    return _to_response(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> None:
    """Delete a project and, by cascade, its sessions.

    The last project is deletable like any other: a caller who empties the list
    creates a new one from the switcher, which handles the empty state.
    """
    project = await _owned_project(project_id, current_user, db)

    await db.delete(project)
    await db.commit()
