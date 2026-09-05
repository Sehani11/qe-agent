"""Reports API routes — traceability report + PDF/CSV export (Stories 5.3, 5.4)."""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.session import Session
from app.schemas.report import TraceabilityReport
from app.services import report_service
from app.services.storage_service import (
    FOLDER_REPORTS,
    StorageServiceError,
    storage_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_ticket_slug(jira_ticket_id: str) -> str:
    """Sanitize a ticket id for use in a filename / Content-Disposition header.

    Keeps alphanumerics, dash, underscore and dot; replaces anything else
    (quotes, control chars, path separators) with '_' so a crafted ticket id
    can't break the header or traverse the storage path. Falls back to 'report'.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", jira_ticket_id or "")
    return slug or "report"


async def _load_owned_session(
    session_id: str, db: AsyncSession, current_user: str
) -> Session:
    """Load a session, enforcing ownership: 404 if missing, 403 if not the owner."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")
    return session


@router.get("/{session_id}/traceability", response_model=TraceabilityReport)
async def get_traceability_report(
    session_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> TraceabilityReport:
    """Return the AC → BDD → verdict traceability report for a session.

    Assembled from verification_results + bdd_files (no LLM). Returns 404 if the
    session does not exist, 403 if it belongs to another user, and a report with
    an empty ``rows`` list when no verification results exist yet.
    """
    session = await _load_owned_session(session_id, db, current_user)
    return await report_service.build_traceability_report(session, db)


@router.get("/{session_id}/export/pdf")
async def export_report_pdf(
    session_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> Response:
    """Generate and download the traceability report as a PDF (Story 5.4).

    Reuses the Story 5.3 assembly (no LLM/Pinecone). The PDF is best-effort
    persisted to the ``reports`` storage bucket (FR42); a storage failure is
    logged but never blocks the download.
    """
    session = await _load_owned_session(session_id, db, current_user)
    report = await report_service.build_traceability_report(session, db)
    pdf_bytes = report_service.render_report_pdf(report)

    filename = f"{_safe_ticket_slug(report.jira_ticket_id)}_report.pdf"
    await _persist_report(
        session_id, current_user, filename, pdf_bytes, "application/pdf"
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{session_id}/export/csv")
async def export_report_csv(
    session_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> Response:
    """Generate and download the traceability report as CSV (Story 5.4).

    Columns: AC Clause, Scenario, Status, Justification, Code Reference,
    Implementation Suggestion, RAG Context. Reuses the Story 5.3 assembly
    (no LLM/Pinecone).
    """
    session = await _load_owned_session(session_id, db, current_user)
    report = await report_service.build_traceability_report(session, db)
    csv_bytes = report_service.render_report_csv(report)

    filename = f"{_safe_ticket_slug(report.jira_ticket_id)}_report.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _persist_report(
    session_id: str,
    user_id: str,
    filename: str,
    data: bytes,
    content_type: str,
) -> None:
    """Best-effort upload of a generated report to the ``reports`` bucket (FR42).

    Never raises: if storage is unconfigured or the upload fails, the error is
    logged and the caller still returns the bytes to the user (Story 5.4 AC3).
    """
    if not storage_service.is_available:
        logger.info("Storage unavailable — skipping persistence of %s", filename)
        return
    try:
        await storage_service.upload_file(
            folder=FOLDER_REPORTS,
            path=f"{session_id}/{filename}",
            file_data=data,
            content_type=content_type,
            user_id=user_id,
        )
    except StorageServiceError as e:
        logger.warning("Failed to persist report %s to storage: %s", filename, e)
