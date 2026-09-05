"""Verification API route — agentic GitHub code verification.

The legacy two-step flow (POST /fetch then POST /run) was removed once the UI
moved entirely to the agentic endpoint: the LLM fetches code on demand via
GitHub tools instead of a pre-fetch step.
"""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.llm_selection import llm_with_tools_for
from app.core.auth import get_current_user
from app.core.database import get_db
from app.schemas.verification import AgenticVerificationRequest
from app.services import agentic_verification_service
from app.services.project_config_service import (
    github_credentials_for,
    load_project,
    vector_config_for,
)

router = APIRouter()


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
      data: {"type": "verdict", ...}\n\n   (per scenario)
      data: {"type": "complete", ...}\n\n  (final summary)
      data: {"type": "error", ...}\n\n     (per-scenario or critical error)
    """
    llm = llm_with_tools_for(request)
    # The token comes from the project when it has one, so a run against a
    # private repository uses that project's access rather than a shared PAT.
    project = await load_project(db, current_user, request.project_id)
    github = github_credentials_for(project)

    stream: AsyncGenerator[str, None] = agentic_verification_service.run_agentic_verification(
        session_id=request.session_id,
        user_id=current_user,
        bdd_content=request.bdd_content,
        mode=request.mode,
        github_input=request.github_input,
        llm=llm,
        db=db,
        pat=github.access_token,
        use_knowledge_base=request.use_knowledge_base,
        project_id=request.project_id,
        # RAG reads this project's index with the vendor it was written by.
        vector_config=vector_config_for(project),
        code_index_enabled=request.code_index_enabled,
    )
    return StreamingResponse(stream, media_type="text/event-stream")
