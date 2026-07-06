"""Sessions API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.bdd_file import BddFile
from app.models.session import Session
from app.models.verification_result import VerificationResult
from app.schemas.session import (
    SessionBDDResponse,
    SessionResponse,
    StoredVerificationResult,
)

router = APIRouter()


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> list[SessionResponse]:
    """Return all sessions belonging to the current user, newest first.

    Each session includes a bdd_status field derived from the latest bdd_files
    row for that session ("none" | "generated" | "uploaded").
    """
    result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user)
        .order_by(Session.created_at.desc())
    )
    sessions = result.scalars().all()

    # Build bdd_status map: session_id -> latest source
    bdd_map: dict[str, str] = {}
    if sessions:
        session_ids = [str(s.id) for s in sessions]
        bdd_rows_result = await db.execute(
            select(BddFile.session_id, BddFile.source, BddFile.created_at)
            .where(BddFile.session_id.in_(session_ids))
            .order_by(BddFile.created_at.desc())
        )
        for row in bdd_rows_result.all():
            # First row per session_id is the newest (ORDER BY created_at DESC)
            if row.session_id not in bdd_map:
                bdd_map[row.session_id] = row.source

    responses: list[SessionResponse] = []
    for s in sessions:
        resp = SessionResponse(
            id=s.id,
            user_id=s.user_id,
            jira_ticket_id=s.jira_ticket_id,
            created_at=s.created_at,
            bdd_status=bdd_map.get(str(s.id), "none"),
        )
        responses.append(resp)
    return responses


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> SessionResponse:
    """Return a single session by ID.

    Returns 404 if not found, 403 if the session belongs to another user.
    """
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")

    # Get latest bdd_status for this session
    bdd_result = await db.execute(
        select(BddFile)
        .where(BddFile.session_id == session_id)
        .order_by(BddFile.created_at.desc())
        .limit(1)
    )
    bdd_file = bdd_result.scalar_one_or_none()

    return SessionResponse(
        id=session.id,
        user_id=session.user_id,
        jira_ticket_id=session.jira_ticket_id,
        created_at=session.created_at,
        bdd_status=bdd_file.source if bdd_file else "none",
    )


@router.get("/{session_id}/bdd", response_model=SessionBDDResponse)
async def get_session_bdd(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> SessionBDDResponse:
    """Return the most recent BDD file content for a session.

    Returns 404 if session not found or no BDD content exists.
    Returns 403 if session belongs to another user.
    """
    # Verify session exists and is owned by current user
    session_result = await db.execute(select(Session).where(Session.id == session_id))
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")

    # Get the latest bdd_files row for this session
    bdd_result = await db.execute(
        select(BddFile)
        .where(BddFile.session_id == session_id)
        .order_by(BddFile.created_at.desc())
        .limit(1)
    )
    bdd_file = bdd_result.scalar_one_or_none()
    if bdd_file is None:
        raise HTTPException(status_code=404, detail="No BDD content found for session.")

    return SessionBDDResponse(
        session_id=str(bdd_file.session_id),
        content=bdd_file.content,
        source=bdd_file.source,
        created_at=bdd_file.created_at,
    )


@router.get(
    "/{session_id}/verification-results",
    response_model=list[StoredVerificationResult],
)
async def get_session_verification_results(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> list[StoredVerificationResult]:
    """Return all stored verification results for a session, oldest first.

    Returns empty list if no results exist.
    Returns 403 if session belongs to another user.
    Returns 404 if session not found.
    """
    # Verify session exists and is owned by current user
    session_result = await db.execute(select(Session).where(Session.id == session_id))
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")

    # Return all verification results for the session, ordered oldest first
    vr_result = await db.execute(
        select(VerificationResult)
        .where(VerificationResult.session_id == session_id)
        .order_by(VerificationResult.created_at.asc())
    )
    results = vr_result.scalars().all()
    return [StoredVerificationResult.model_validate(r) for r in results]
