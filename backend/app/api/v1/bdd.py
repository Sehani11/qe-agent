"""BDD API Generation and upload endpoints."""

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.v1.llm_selection import (
    llm_for,
    validate_bdd_selection,
    validate_selection,
)
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import AsyncSession, get_db
from app.models.bdd_file import BddFile
from app.models.session import Session
from app.schemas.bdd import (
    BDDCoverage,
    BDDGenerateRequest,
    BDDGenerateResult,
    BDDSaveRequest,
    BDDSaveResponse,
    BDDUploadResponse,
    FineTunedStatus,
)
from app.services import fine_tuned_serving
from app.services.bdd_coverage import coverage_report
from app.services.bdd_service import BDDServiceError, generate_bdd_scenarios
from app.services.project_config_service import ensure_project
from app.services.storage_service import (
    FOLDER_FEATURE_FILES,
    StorageServiceError,
    storage_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_UPLOAD_BYTES = 512 * 1024  # 512 KB


def _resolve_training_opt_in(requested: bool | None) -> bool:
    """Combine the deployment's training policy with this request's consent.

    The two are ANDed, so a client can only ever NARROW the policy:

      - Operator says no  -> no, whatever the request asks. A deployment that
        may not train on its users' data cannot have that overridden by a
        checkbox, a stale client, or a hand-made request.
      - Operator says yes -> the person capturing the content decides.

    Keeps the fail-closed property the column was built around: an absent or
    malformed flag lands on the deployment's actual choice rather than on
    "yes, train on this".
    """
    if not settings.training_data_opt_in:
        return False
    return True if requested is None else bool(requested)


@router.post("/generate", response_model=BDDGenerateResult)
async def generate_bdd(
    request: BDDGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> BDDGenerateResult:
    """Generate Gherkin BDD scenarios from acceptance criteria and persist to bdd_files.

    Acceptance criteria text is evaluated against the LLM provider. Generates
    a fully structured JSON response mapping each clause to scenario context.
    The full response is persisted as JSON in the bdd_files table for retrieval
    in Story 3.5 (scenario metadata + AC traceability are preserved).
    """
    validate_selection(request)
    validate_bdd_selection(request.bdd_model_provider)

    # Build the provider here purely for the credential check it performs.
    #
    # The general-LLM path resolves its key several calls deep, inside the BDD
    # provider layer, where a missing one is caught by bdd_service's catch-all
    # and returned as a 500 — for a setting the operator can fix in a line.
    # `llm_for` turns exactly that into a 400 naming the variable to set, and
    # every other endpoint already goes through it.
    #
    # Skipped for `fine_tuned`, which serves its own model over HTTP and needs
    # no LLM credential. It can still fall back to the general LLM, but that
    # path degrades and reports on its own; refusing here would block a working
    # fine-tuned-only deployment over a key it never uses.
    effective_bdd_provider = (
        request.bdd_model_provider or settings.bdd_model_provider or ""
    ).strip().lower()
    if effective_bdd_provider != "fine_tuned":
        llm_for(request)

    try:
        response = await generate_bdd_scenarios(
            session_id=request.session_id,
            acceptance_criteria=request.acceptance_criteria,
            llm_provider=request.llm_provider,
            llm_model=request.llm_model,
            bdd_model_provider=request.bdd_model_provider,
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
            # Story 6.4 — the input half of the training pair. Kept on this row
            # so the pair stays correct even if the Jira ticket changes later.
            acceptance_criteria=request.acceptance_criteria,
            content_format="json",
            # Story 6.6 — stamped at write time, never re-evaluated later.
            training_opt_in=_resolve_training_opt_in(request.training_opt_in),
        )
        db.add(bdd_file)
        await db.commit()

        # Coverage is measured against the criteria as SUBMITTED, so a clause
        # that never reached the model still counts against the score. It is
        # computed here rather than inside bdd_service because it describes the
        # request, not the model call, and it is deliberately not part of
        # BDDGenerateResponse — that schema is the model's response_format, and
        # a model must never grade its own coverage.
        coverage = coverage_report(
            request.acceptance_criteria,
            [s.model_dump() for s in response.scenarios],
        )
        if coverage["uncovered"]:
            logger.warning(
                "bdd.coverage_gap session_id=%s covered=%d/%d uncovered=%s",
                request.session_id,
                coverage["covered_clauses"],
                coverage["total_clauses"],
                ",".join(coverage["uncovered"]),
            )
        else:
            logger.info(
                "bdd.coverage session_id=%s covered=%d/%d",
                request.session_id,
                coverage["covered_clauses"],
                coverage["total_clauses"],
            )

        return BDDGenerateResult(
            scenarios=response.scenarios,
            coverage=BDDCoverage(**coverage),
        )

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


@router.post("/save", response_model=BDDSaveResponse)
async def save_bdd(
    request: BDDSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> BDDSaveResponse:
    """Persist edited BDD content as a new row, preserving the original.

    Story 6.4. A human correcting generated output is the highest-value
    training signal available — it says exactly where the model was wrong and
    what the right answer looks like. Both versions must survive, so this
    always INSERTs; it never updates the generated row.

    The originating generated row is resolved here rather than supplied by the
    client: returning its id from /generate would mean adding a field to
    BDDGenerateResponse, whose JSON schema is passed to the model as
    `response_format` — the LLM would then be asked to fill it in.
    """
    if len(request.content.encode("utf-8")) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Content too large. Maximum size is {_MAX_UPLOAD_BYTES // 1024} KB."
            ),
        )

    result = await db.execute(
        select(Session).where(Session.id == str(request.session_id))
    )
    session = result.scalar_one_or_none()

    # Unlike /upload, do NOT create a session on the fly — an edit with no
    # session to belong to is a client bug, not a supported flow.
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")

    # Most recent generated row for this session, if any. None is valid: the
    # user may have edited uploaded content that never had a generated ancestor.
    parent_result = await db.execute(
        select(BddFile)
        .where(
            BddFile.session_id == str(request.session_id),
            # Scoped by user as well as session: the parent's acceptance
            # criteria are copied onto a row owned by the caller, so this must
            # never reach across users even if session data is inconsistent.
            BddFile.user_id == current_user,
            BddFile.source == "generated",
        )
        .order_by(BddFile.created_at.desc())
        .limit(1)
    )
    parent = parent_result.scalar_one_or_none()

    # id/created_at are assigned here rather than left to the column defaults,
    # which only materialise at flush — this endpoint returns them, so they must
    # not depend on flush timing.
    bdd_file = BddFile(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        session_id=str(request.session_id),
        user_id=current_user,
        content=request.content,
        source="edited",
        content_format="gherkin",
        parent_id=parent.id if parent is not None else None,
        # Carry the input half forward so each row is a self-contained pair.
        acceptance_criteria=parent.acceptance_criteria if parent is not None else None,
        training_opt_in=_resolve_training_opt_in(request.training_opt_in),
    )
    db.add(bdd_file)
    await db.commit()

    return BDDSaveResponse(
        id=bdd_file.id,
        session_id=str(request.session_id),
        source="edited",
        created_at=bdd_file.created_at,
    )


@router.post("/upload", response_model=BDDUploadResponse)
async def upload_bdd(
    session_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    training_opt_in: bool | None = Form(default=None),
    project_id: uuid.UUID | None = Form(default=None),
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
        # sessions.project_id is NOT NULL, so an upload into a fresh session id
        # still has to land in a project — the caller's, or their default.
        project = await ensure_project(db, current_user, project_id)
        session = Session(
            id=str(session_id),
            user_id=current_user,
            project_id=project.id,
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
        content_format="gherkin",  # raw .feature text, not a serialized response
        # Uploaded rows are build_dataset.py's default source, so the opt-out
        # would be largely meaningless if it skipped this path.
        training_opt_in=_resolve_training_opt_in(training_opt_in),
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


@router.get("/fine-tuned-status", response_model=FineTunedStatus)
async def fine_tuned_status(
    current_user: str = Depends(get_current_user),
) -> FineTunedStatus:
    """Report whether the fine-tuned model behind the toggle is being served.

    The toggle is a stored preference and the generate path falls back to the
    general LLM whenever the endpoint cannot answer. Together that means a
    switch reading "use fine-tuned model" can be on while every scenario comes
    from somewhere else — silently, and most visibly right after a wipe removes
    the model. This is what lets the control say so instead.

    Always 200: "there is no fine-tuned model" is an answer, not a failure, and
    a client rendering a toggle should not have to tell the two apart.
    """
    return FineTunedStatus(**await fine_tuned_serving.status())  # type: ignore[arg-type]
