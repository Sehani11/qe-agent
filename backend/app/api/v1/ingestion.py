"""Ingestion API route for Jira tickets using a standard JSON response."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import get_db, AsyncSession
from app.core.auth import get_current_user
from app.models.session import Session
from app.schemas.ingestion import IngestRequest, IngestResponse
from app.services.jira_service import fetch_ticket_content, JiraServiceError
from app.services.vector_service import embed_and_index_ticket, VectorServiceError


router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest_ticket(
    request: IngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> IngestResponse:
    """Ingest a Jira ticket and return the session payload needed for BDD generation."""
    try:
        ticket_data = await fetch_ticket_content(request.ticket_id_or_url)

        db_session = Session(
            user_id=current_user,
            jira_ticket_id=ticket_data.ticket_id,
        )
        db.add(db_session)
        await db.commit()

        await embed_and_index_ticket(
            session_id=str(db_session.id),
            ticket_id=ticket_data.ticket_id,
            summary=ticket_data.summary,
            description=ticket_data.description,
            acceptance_criteria=ticket_data.acceptance_criteria,
        )

        return IngestResponse(
            session_id=str(db_session.id),
            jira_ticket_id=ticket_data.ticket_id,
            acceptance_criteria=ticket_data.acceptance_criteria,
        )
    except JiraServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except VectorServiceError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
