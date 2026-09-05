"""Sessions API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.bdd_file import BddFile
from app.models.chat_message import ChatMessage
from app.models.session import Session
from app.models.verification_result import VerificationResult
from app.schemas.session import (
    SessionBDDResponse,
    SessionListResponse,
    SessionResponse,
    StoredVerificationResult,
)

router = APIRouter()

# A page big enough that most users never page at all, small enough that the
# bdd_status lookup below stays a short IN-list.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    limit: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Maximum sessions to return.",
    ),
    offset: int = Query(0, ge=0, description="Sessions to skip, newest first."),
    project_id: uuid.UUID | None = Query(
        None, description="Only sessions in this project. Omit for all of them."
    ),
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> SessionListResponse:
    """Return one page of this project's sessions, newest first.

    Each session includes a bdd_status field derived from the latest bdd_files
    row for that session ("none" | "generated" | "uploaded" | "edited").

    The total is counted before the page is fetched so an offset past the end
    still reports how many sessions exist, letting the client correct itself
    rather than showing an empty list as "no sessions".

    The project filter is applied to BOTH the count and the page. Filtering only
    the page would leave the pager sized for every project, so it would offer
    pages that come back empty.
    """
    scope = [Session.user_id == current_user]
    if project_id is not None:
        scope.append(Session.project_id == project_id)

    total = await db.scalar(
        select(func.count()).select_from(Session).where(*scope)
    )

    result = await db.execute(
        select(Session)
        .where(*scope)
        # id breaks ties: sessions created in the same instant would otherwise
        # order arbitrarily per query, so a row could repeat on one page and be
        # skipped on the next.
        .order_by(Session.created_at.desc(), Session.id.desc())
        .limit(limit)
        .offset(offset)
    )
    sessions = result.scalars().all()

    session_ids = [str(s.id) for s in sessions]

    # Build bdd_status map: session_id -> latest source
    bdd_map: dict[str, str] = {}
    if sessions:
        bdd_rows_result = await db.execute(
            select(BddFile.session_id, BddFile.source, BddFile.created_at)
            .where(BddFile.session_id.in_(session_ids))
            .order_by(BddFile.created_at.desc())
        )
        for row in bdd_rows_result.all():
            # First row per session_id is the newest (ORDER BY created_at DESC)
            if row.session_id not in bdd_map:
                bdd_map[row.session_id] = row.source

    # Which of these sessions have been verified. One DISTINCT query for the
    # page rather than a per-row EXISTS, matching how bdd_status is built above:
    # a page of 20 would otherwise be 20 extra round trips.
    verified_ids: set[str] = set()
    if sessions:
        verified_result = await db.execute(
            select(VerificationResult.session_id)
            .where(VerificationResult.session_id.in_(session_ids))
            .distinct()
        )
        verified_ids = {row[0] for row in verified_result.all()}

    responses: list[SessionResponse] = []
    for s in sessions:
        resp = SessionResponse(
            id=s.id,
            user_id=s.user_id,
            jira_ticket_id=s.jira_ticket_id,
            jira_ticket_url=s.jira_ticket_url,
            created_at=s.created_at,
            bdd_status=bdd_map.get(str(s.id), "none"),
            verification_status=(
                "completed" if str(s.id) in verified_ids else "none"
            ),
        )
        responses.append(resp)
    return SessionListResponse(
        items=responses,
        total=total or 0,
        limit=limit,
        offset=offset,
    )


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

    # Existence only — the verdicts themselves are served by /verification.
    # `limit(1)` so a session with hundreds of rows costs the same as one row.
    verified_result = await db.execute(
        select(VerificationResult.id)
        .where(VerificationResult.session_id == session_id)
        .limit(1)
    )
    is_verified = verified_result.scalar_one_or_none() is not None

    return SessionResponse(
        id=session.id,
        user_id=session.user_id,
        jira_ticket_id=session.jira_ticket_id,
        jira_ticket_url=session.jira_ticket_url,
        created_at=session.created_at,
        bdd_status=bdd_file.source if bdd_file else "none",
        verification_status="completed" if is_verified else "none",
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


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> None:
    """Delete a session and everything scoped to it.

    session_id on bdd_files, verification_results, and chat_messages is a plain
    string column, not a foreign key — there is no DB-level cascade, so those
    rows are removed explicitly before the session itself.

    Returns 404 if not found, 403 if the session belongs to another user.
    """
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")

    await db.execute(delete(BddFile).where(BddFile.session_id == session_id))
    await db.execute(
        delete(VerificationResult).where(VerificationResult.session_id == session_id)
    )
    await db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.delete(session)
    await db.commit()


@router.delete("")
async def delete_all_sessions(
    project_id: uuid.UUID | None = Query(
        None, description="Only sessions in this project. Omit for all of them."
    ),
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> JSONResponse:
    """Delete every one of the caller's sessions, optionally scoped to a project.

    Mirrors DELETE /knowledge/sources: one call rather than looping the
    per-session delete, so clearing a project's whole history costs one round
    trip instead of one per session. Returns ``{"deleted": N}`` — 0 is a
    success, not a 404.
    """
    scope = [Session.user_id == current_user]
    if project_id is not None:
        scope.append(Session.project_id == project_id)

    result = await db.execute(select(Session.id).where(*scope))
    ids = result.scalars().all()

    if ids:
        str_ids = [str(i) for i in ids]
        await db.execute(delete(BddFile).where(BddFile.session_id.in_(str_ids)))
        await db.execute(
            delete(VerificationResult).where(
                VerificationResult.session_id.in_(str_ids)
            )
        )
        await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(str_ids)))
        await db.execute(delete(Session).where(Session.id.in_(ids)))
        await db.commit()

    return JSONResponse(status_code=200, content={"deleted": len(ids)})
