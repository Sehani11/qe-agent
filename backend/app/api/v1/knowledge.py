"""Knowledge base API routes.

Handles project knowledge source ingestion (Confluence, Jira) into the
per-user vector database namespace.
"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.knowledge_source import KnowledgeSource
from app.schemas.knowledge import (
    CodeIndexRequest,
    CodeIndexStatusResponse,
    ConfluenceIngestRequest,
    DocumentIngestResponse,
    JiraIngestRequest,
    KnowledgeSourceResponse,
)
from app.services import code_index_service, knowledge_service
from app.services.github_service import GitHubServiceError, _parse_repo_url
from app.services.knowledge_service import DocumentIngestError
from app.services.project_config_service import (
    github_credentials_for,
    load_project,
    vector_config_for,
)

router = APIRouter()

# Max upload size for knowledge documents (10 MB).
_MAX_DOC_BYTES = 10 * 1024 * 1024


@router.get("/sources", response_model=list[KnowledgeSourceResponse])
async def list_sources(
    project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> list[KnowledgeSourceResponse]:
    """Return this project's ingested knowledge sources, newest first.

    Scoped to ``current_user`` so no cross-user sources are ever returned
    (NFR-S8), and to ``project_id`` because each project has its own vector
    namespace — listing across projects would offer deletes that reach into a
    namespace the row does not live in.

    Omitting ``project_id`` lists the pre-projects rows (those with no project),
    which is what a client that has not been updated still sees.

    The code index is excluded. It is one row describing a whole repository
    rather than a document someone ingested, it has its own card reporting the
    commit it was built at, and listing it here would offer a Delete beside the
    Re-index that is almost always the action actually wanted.
    """
    result = await db.execute(
        select(KnowledgeSource)
        .where(
            KnowledgeSource.user_id == current_user,
            KnowledgeSource.project_id == project_id,
            KnowledgeSource.source_type != "code",
        )
        .order_by(KnowledgeSource.created_at.desc())
    )
    rows = result.scalars().all()
    return [KnowledgeSourceResponse.model_validate(r) for r in rows]


@router.delete("/sources")
async def delete_all_sources(
    project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> JSONResponse:
    """Delete every knowledge source in one project.

    Scoped to ``current_user`` — there is no way to ask for another user's rows,
    so this needs no per-row ownership check — and to ``project_id``, so
    clearing one project's knowledge base leaves the others intact. Omitting
    ``project_id`` clears the pre-projects rows, which is what an un-updated
    client still targets.

    Wipes that project's Pinecone namespace in one call rather than looping the
    per-source delete, which also clears vectors that no per-source delete could
    target (legacy rows with no ``source_ref``, and orphans from earlier
    failures).

    Returns ``{"deleted": N}``. N is 0 when there was nothing to delete, which
    is a success, not a 404.
    """
    deleted = await knowledge_service.delete_all_knowledge_sources(
        current_user, db, project_id
    )
    return JSONResponse(status_code=200, content={"deleted": deleted})


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> Response:
    """Delete an ingested knowledge source and its vectors (Story 4.7).

    Removes the source's vectors from the namespace of the project it was
    ingested into (best-effort) and the ``knowledge_sources`` row. 404 if the
    source does not exist, 403 if it belongs to another user (NFR-S8). Returns
    204 on success.
    """
    # A malformed (non-UUID) id can't match any row — treat as not found rather
    # than letting the DB driver raise a cast error (500).
    try:
        uuid.UUID(source_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="Knowledge source not found."
        ) from exc

    result = await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found.")
    if source.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")

    await knowledge_service.delete_knowledge_source(source, db)
    return Response(status_code=204)


@router.post("/ingest/confluence")
async def ingest_confluence(
    request: ConfluenceIngestRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Ingest Confluence pages into the user's knowledge base.

    Streams SSE progress events while fetching, chunking, and embedding pages
    into Pinecone under namespace ``{user_id}:knowledge``.

    Requires ``page_refs`` (page URLs / ids), ``space_key``, or ``page_id``.

    Streams events in SSE format:
      data: {"type": "progress", "message": "...", "current": N, "total": M}\\n\\n
      data: {"type": "complete", "ingested_count": N}\\n\\n
      data: {"type": "error", "error": "CODE", "message": "..."}\\n\\n
    """
    if not request.space_key and not request.page_id and not request.page_refs:
        return JSONResponse(
            status_code=422,
            content={
                "error": "INVALID_REQUEST",
                "message": "Provide page_refs, space_key or page_id.",
                "code": 422,
            },
        )

    stream = knowledge_service.ingest_confluence(
        user_id=current_user,
        request=request,
        db=db,
    )
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/ingest/jira")
async def ingest_jira(
    request: JiraIngestRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Ingest Jira project tickets into the user's knowledge base.

    Streams SSE progress events while fetching, chunking, and embedding tickets
    into Pinecone under namespace ``{user_id}:knowledge``.

    Requires ``project_key`` in the request body. ``sprint`` and ``label`` are
    optional JQL filters.

    Streams events in SSE format:
      data: {"type": "progress", "message": "...", "current": N, "total": M}\\n\\n
      data: {"type": "complete", "ingested_count": N}\\n\\n
      data: {"type": "error", "error": "CODE", "message": "..."}\\n\\n
    """
    stream = knowledge_service.ingest_jira(
        user_id=current_user,
        request=request,
        db=db,
    )
    return StreamingResponse(stream, media_type="text/event-stream")


@router.get("/index/code", response_model=CodeIndexStatusResponse)
async def code_index_status(
    project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> CodeIndexStatusResponse:
    """Report whether this project has a code index, and what it was built from.

    Scoped to ``current_user`` and ``project_id`` for the same reason the source
    list is: a code index lives in one project's Pinecone namespace, so another
    project's status would describe vectors this project cannot read.
    """
    record = await code_index_service.get_code_index(db, current_user, project_id)
    if record is None or not record.indexed_ref:
        return CodeIndexStatusResponse(indexed=False)
    return CodeIndexStatusResponse(
        indexed=True,
        repo=record.source_ref,
        indexed_ref=record.indexed_ref,
        file_count=record.page_count,
        indexed_at=record.created_at,
    )


@router.post("/index/code")
async def index_code(
    request: CodeIndexRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Index a repository's source into this project's ``:code`` namespace.

    Streams SSE progress while resolving the ref, downloading the repository,
    chunking and embedding:
      data: {"type": "progress", "message": "...", "current": N, "total": M}\\n\\n
      data: {"type": "complete", "indexed_files": N, "chunks": M, "sha": "..."}\\n\\n
      data: {"type": "error", "error": "CODE", "message": "..."}\\n\\n

    Re-indexing the same project replaces what is there: each file's vectors are
    deleted before its new ones are written, so a file that shrank leaves no
    stale tail chunks behind to answer retrieval with deleted code.
    """
    try:
        owner, name = _parse_repo_url(request.repo_url)
    except GitHubServiceError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "error": "INVALID_REPO",
                "message": exc.message,
                "code": 422,
            },
        )

    # The same project decides which token reads the repository, how its text is
    # embedded, and which index holds it — the trio that has to agree for the
    # verification read path to find anything.
    project = await load_project(db, current_user, request.project_id)

    stream = code_index_service.index_repository(
        user_id=current_user,
        project_id=request.project_id,
        repo=f"{owner}/{name}",
        ref=request.ref,
        pat=github_credentials_for(project).access_token,
        db=db,
        config=vector_config_for(project),
    )
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/ingest/document", response_model=DocumentIngestResponse)
async def ingest_document(
    file: UploadFile = File(...),  # noqa: B008
    project_id: uuid.UUID | None = Form(default=None),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> DocumentIngestResponse:
    """Ingest an uploaded PDF/DOCX document into this project's knowledge base.

    Extracts text, chunks and embeds it into the project's Pinecone namespace
    with ``source="document"`` (Story 4.6).

    Returns 422 for unsupported types / empty documents, 413 for oversize files.
    """
    filename = file.filename or ""
    if not (filename.lower().endswith(".pdf") or filename.lower().endswith(".docx")):
        raise HTTPException(
            status_code=422,
            detail="Only .pdf and .docx files are accepted.",
        )

    max_mb = _MAX_DOC_BYTES // (1024 * 1024)
    # Reject oversize uploads up-front (from the reported size) before buffering
    # the whole body, then re-check the actual bytes as defense-in-depth.
    if file.size is not None and file.size > _MAX_DOC_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {max_mb} MB.",
        )

    data = await file.read()
    if len(data) > _MAX_DOC_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {max_mb} MB.",
        )

    try:
        result = await knowledge_service.ingest_document(
            user_id=current_user,
            filename=filename,
            data=data,
            db=db,
            project_id=project_id,
        )
    except DocumentIngestError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc

    return DocumentIngestResponse(**result)
