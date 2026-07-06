"""Verification API routes for GitHub code fetching and LLM verification."""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.schemas.verification import (
    AgenticVerificationRequest,
    VerificationFetchRequest,
    VerificationFetchResponse,
    VerificationRunRequest,
)
from app.services import agentic_verification_service, verification_service
from app.services.github_service import GitHubServiceError, fetch_github_code
from app.services.llm.factory import get_llm_provider

router = APIRouter()


@router.post("/fetch")
async def fetch_verification_code(
    request: VerificationFetchRequest,
    current_user: str = Depends(get_current_user),
) -> JSONResponse:
    """Fetch code from GitHub based on the selected verification mode.

    Supports three modes:
    - exact_files: fetch specific files by GitHub blob URL (one per line)
    - full_repo:   fetch all text files in a repository (capped at 100)
    - pull_request: fetch PR diff/patch for changed files

    No LLM call is made here — fetched code is returned to the frontend
    for use in the LLM verification step (Story 2.3).

    Error envelope matches the architecture standard:
      {"error": "<CODE>", "message": "<human-readable>", "code": 422}
    """
    try:
        fetched_files = await fetch_github_code(
            mode=request.mode,
            github_input=request.github_input,
            pat=settings.github_access_token,
        )
    except GitHubServiceError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "error": exc.code,
                "message": exc.message,
                "code": 422,
            },
        )

    response_data = VerificationFetchResponse(
        session_id=request.session_id,
        mode=request.mode,
        fetched_files=fetched_files,
    )
    return JSONResponse(content=response_data.model_dump())


@router.post("/run")
async def run_verification_endpoint(
    request: VerificationRunRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Run LLM verification and stream per-scenario verdicts as SSE.

    Streams events in SSE format:
      data: {"type": "verdict", ...}\\n\\n   (per scenario)
      data: {"type": "complete", ...}\\n\\n  (final summary)
      data: {"type": "error", ...}\\n\\n     (per-scenario or critical error)
    """
    llm = get_llm_provider()
    stream: AsyncGenerator[str, None] = verification_service.run_verification(
        session_id=request.session_id,
        user_id=current_user,
        bdd_content=request.bdd_content,
        fetched_files=request.fetched_files,
        llm=llm,
        db=db,
    )
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/run-agentic")
async def run_agentic_verification_endpoint(
    request: AgenticVerificationRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Run agentic LLM verification and stream per-scenario verdicts as SSE.

    The LLM calls GitHub tools on demand to fetch relevant code, then
    produces a verdict for each scenario. No pre-fetch step is required.

    Streams events in SSE format:
      data: {"type": "verdict", ...}\\n\\n   (per scenario)
      data: {"type": "complete", ...}\\n\\n  (final summary)
      data: {"type": "error", ...}\\n\\n     (per-scenario or critical error)
    """
    llm = get_llm_provider()
    stream: AsyncGenerator[str, None] = agentic_verification_service.run_agentic_verification(
        session_id=request.session_id,
        user_id=current_user,
        bdd_content=request.bdd_content,
        mode=request.mode,
        github_input=request.github_input,
        llm=llm,
        db=db,
        pat=settings.github_access_token,
    )
    return StreamingResponse(stream, media_type="text/event-stream")
