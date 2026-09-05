"""Chat API routes — RAG ticket comprehension (Story 5.1)."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.llm_selection import llm_for
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.chat_message import ChatMessage
from app.models.session import Session
from app.schemas.chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    KnowledgeChatRequest,
)
from app.services import chat_service, knowledge_chat_service
from app.services.project_config_service import load_project, vector_config_for

router = APIRouter()


async def _verify_session_owner(
    session_id: str, current_user: str, db: AsyncSession
) -> None:
    """Raise 404 if the session doesn't exist, 403 if it belongs to another user."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")


@router.post("/message")
async def post_message(
    request: ChatMessageRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Answer a question about the session's ingested ticket, streamed as SSE.

    Retrieves ticket context from namespace ``{user_id}:{session_id}`` and
    grounds the LLM in it. Streams ``token`` events then ``complete``.
    """
    await _verify_session_owner(request.session_id, current_user, db)

    llm = llm_for(request)
    stream = chat_service.stream_chat_answer(
        session_id=request.session_id,
        user_id=current_user,
        question=request.question,
        llm=llm,
        db=db,
    )
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/knowledge")
async def post_knowledge_message(
    request: KnowledgeChatRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Answer a project-wide question from the user's knowledge base, as SSE.

    Retrieves context from the user's ``{user_id}:knowledge`` namespace (Epic 4
    ingested sources) and grounds the LLM in it. Streams ``token`` events, then a
    ``sources`` event, then ``complete``. Not scoped to a session; stateless.
    """
    llm = llm_for(request)
    # The knowledge base is read with the vendor and index its project was
    # ingested with — resolved here, not taken from the request.
    project = await load_project(db, current_user, request.project_id)
    stream = knowledge_chat_service.stream_knowledge_answer(
        user_id=current_user,
        question=request.question,
        llm=llm,
        project_id=request.project_id,
        config=vector_config_for(project),
    )
    return StreamingResponse(stream, media_type="text/event-stream")


@router.get("/{session_id}/messages", response_model=list[ChatMessageResponse])
async def list_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> list[ChatMessageResponse]:
    """Return the session's chat history (oldest first). Empty list if none."""
    await _verify_session_owner(session_id, current_user, db)

    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return [ChatMessageResponse.model_validate(m) for m in result.scalars().all()]
