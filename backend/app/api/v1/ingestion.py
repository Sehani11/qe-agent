"""Ingestion API route for Jira tickets using a standard JSON response."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.core.database import AsyncSession, get_db
from app.models.session import Session
from app.schemas.ingestion import IngestRequest, IngestResponse
from app.services.jira_service import JiraServiceError, fetch_ticket_content
from app.services.project_config_service import (
    ensure_project,
    jira_credentials_for,
    vector_config_for,
)
from app.services.vector_service import VectorServiceError, embed_and_index_ticket

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest_ticket(
    request: IngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> IngestResponse:
    """Ingest a Jira ticket and return the session payload needed for BDD generation."""
    submitted_ref = request.ticket_id_or_url.strip()
    try:
        # Resolved before the call so the ticket is fetched from THIS project's
        # Jira rather than whichever one the environment names.
        project = await ensure_project(db, current_user, request.project_id)
        ticket_data = await fetch_ticket_content(
            submitted_ref, jira_credentials_for(project)
        )

        db_session = Session(
            user_id=current_user,
            project_id=project.id,
            jira_ticket_id=ticket_data.ticket_id,
            jira_ticket_url=submitted_ref,
        )
        db.add(db_session)
        await db.commit()

        await embed_and_index_ticket(
            session_id=str(db_session.id),
            ticket_id=ticket_data.ticket_id,
            summary=ticket_data.summary,
            description=ticket_data.description,
            acceptance_criteria=ticket_data.acceptance_criteria,
            user_id=current_user,
            # This project's vendor and index. Chat re-resolves the same config
            # from the session's project when it reads these vectors back.
            config=vector_config_for(project),
        )

        return IngestResponse(
            session_id=str(db_session.id),
            jira_ticket_id=ticket_data.ticket_id,
            jira_ticket_url=submitted_ref,
            acceptance_criteria=ticket_data.acceptance_criteria,
        )
    except JiraServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except VectorServiceError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
