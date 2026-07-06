"""BDD API Generation and upload endpoints."""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.auth import get_current_user
from app.core.database import AsyncSession, get_db
from app.models.bdd_file import BddFile
from app.models.session import Session
from app.schemas.bdd import BDDGenerateRequest, BDDGenerateResponse, BDDUploadResponse
from app.services.bdd_service import BDDServiceError, generate_bdd_scenarios
from app.services.storage_service import (
    FOLDER_FEATURE_FILES,
    StorageServiceError,
    storage_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_UPLOAD_BYTES = 512 * 1024  # 512 KB


@router.post("/generate", response_model=BDDGenerateResponse)
async def generate_bdd(
    request: BDDGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> BDDGenerateResponse:
    """Generate Gherkin BDD scenarios from acceptance criteria and persist to bdd_files.

    Acceptance criteria text is evaluated against the LLM provider. Generates
    a fully structured JSON response mapping each clause to scenario context.
    The full response is persisted as JSON in the bdd_files table for retrieval
    in Story 3.5 (scenario metadata + AC traceability are preserved).
    """
    try:
        response = await generate_bdd_scenarios(
            session_id=request.session_id,
            acceptance_criteria=request.acceptance_criteria,
        )

        if not response.scenarios:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "BDD_GENERATION_EMPTY",
                    "message": (
                        "The model returned no scenarios. "
                        "Ensure the Jira ticket has meaningful acceptance criteria or description."
                    ),
                    "code": 422,
                },
            )

        bdd_file = BddFile(
            session_id=str(request.session_id),
            user_id=current_user,
            content=json.dumps(response.model_dump()),
            source="generated",
        )
        db.add(bdd_file)
        await db.commit()

        return response

    except BDDServiceError as e:
        # Wrap into the global exception handler envelope pattern
        # by raising an HTTPException properly. The global handler will
        # intercept and return: {"error": "HTTP_ERROR", "message": "...", "code": 500}
        # But we need exactly: {"error": "BDD_GENERATION_FAILED", "message": "...", "code": 500}
        # To accomplish this generically via the existing http_exception_handler,
        # we can't easily alter the outer 'error' key unless we return JSONResponse directly.
        return JSONResponse(
            status_code=500,
            content={
                "error": "BDD_GENERATION_FAILED",
                "message": str(e),
                "code": 500,
            },
        )


@router.post("/upload", response_model=BDDUploadResponse)
async def upload_bdd(
    session_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> BDDUploadResponse:
    """Accept a .feature file upload, persist content to bdd_files with source='uploaded'.

    If a session row with the given id exists, ownership is checked. If no row
    exists, one is created on-the-fly with a placeholder jira_ticket_id derived
    from the filename — supports the "upload first, no Jira ticket" flow where
    a user lands on a fresh /session/<uuid> URL and uploads directly.
    """
    if not file.filename or not file.filename.endswith(".feature"):
        raise HTTPException(
            status_code=422,
            detail="Only .feature files are accepted.",
        )

    raw = await file.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {_MAX_UPLOAD_BYTES // 1024} KB.",
        )

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="File must be valid UTF-8 encoded text.",
        ) from exc

    # Look up the session. If absent, create it; if present, enforce ownership.
    result = await db.execute(select(Session).where(Session.id == str(session_id)))
    session = result.scalar_one_or_none()
    if session is None:
        placeholder_ticket = f"Manual upload: {file.filename}"
        session = Session(
            id=str(session_id),
            user_id=current_user,
            jira_ticket_id=placeholder_ticket,
        )
        db.add(session)
        await db.flush()
    elif session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")

    bdd_file = BddFile(
        session_id=str(session_id),
        user_id=current_user,
        content=content,
        source="uploaded",
    )
    db.add(bdd_file)
    await db.commit()

    # Also persist the raw file to Supabase Storage (non-fatal — PostgreSQL is primary)
    storage_path = f"{session_id}/{file.filename}"
    try:
        await storage_service.upload_file(
            folder=FOLDER_FEATURE_FILES,
            path=storage_path,
            file_data=raw,
            content_type="text/plain",
            user_id=current_user,
        )
    except StorageServiceError as e:
        logger.warning(
            "Supabase Storage upload skipped (PostgreSQL persisted): %s", e.message
        )

    return BDDUploadResponse(
        session_id=str(session_id),
        content=content,
        jira_ticket_id=session.jira_ticket_id,
    )
