"""RAG chat service — answers questions grounded in an ingested Jira ticket (Story 5.1).

Retrieves ticket chunks from the per-session Pinecone namespace
``{user_id}:{session_id}`` (NOT the Epic 4 knowledge base), grounds the LLM
strictly in that context, streams the answer as SSE, and persists the
question/answer pair to ``chat_messages``.

Architecture rules enforced here:
- LLM access only via the LLMProvider interface (never the SDK directly)
- Pinecone queried only in the caller's own {user_id}:{session_id} namespace
- No business logic in the route handler
"""

import asyncio
import json
import logging

import pinecone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat_message import ChatMessage
from app.models.session import Session
from app.services.knowledge_service import _embed_chunks
from app.services.llm.provider import LLMProvider, LLMProviderError
from app.services.project_config_service import load_project, vector_config_for
from app.services.vector_service import index_name_for

logger = logging.getLogger(__name__)

_TOP_K = 5


async def _vector_config_for_session(
    session_id: str, user_id: str, db: AsyncSession
):
    """The embedding vendor and index this session's ticket was indexed with.

    Chat is the one retrieval path with no project in hand — it is addressed by
    session — so the project is resolved through the session row here rather
    than threaded down from the route.

    Falls back to the deployment settings when the session has no project or
    cannot be read: that is exactly where a pre-projects session's vectors are,
    so the fallback finds them rather than searching an empty index.
    """
    try:
        result = await db.execute(
            select(Session.project_id).where(
                Session.id == session_id, Session.user_id == user_id
            )
        )
        project_id = result.scalar_one_or_none()
        project = await load_project(db, user_id, project_id)
    except Exception:
        logger.warning(
            "Could not resolve the project for session=%s; using deployment "
            "embedding settings",
            session_id,
            exc_info=True,
        )
        return vector_config_for(None)

    return vector_config_for(project)

_SYSTEM_PROMPT = """\
You are a helpful assistant answering questions about a single Jira ticket.

Answer using ONLY the information in the CONTEXT below. If the CONTEXT does not
contain the answer, reply that the ticket does not contain that information — do
NOT use outside knowledge, do NOT guess, and do NOT reference anything not in the
CONTEXT."""


def _sse(payload: dict) -> str:
    """Serialize a dict as an SSE data line."""
    return f"data: {json.dumps(payload)}\n\n"


async def _retrieve_ticket_chunks(
    user_id: str, session_id: str, question: str, config=None
) -> list[str]:
    """Return up to _TOP_K ticket chunk texts for the question.

    Scoped strictly to namespace {user_id}:{session_id}. Returns [] on any
    failure or when Pinecone is not configured (graceful degradation).

    `config` must be the `VectorConfig` this session's ticket was INDEXED with
    — the same project's. Embed the question with a different vendor and the
    result is not a failure but an empty one: the vector simply matches nothing
    stored, and the answer comes back ungrounded with no error to explain it.
    """
    if not settings.pinecone_api_key or not question.strip():
        return []

    namespace = f"{user_id}:{session_id}"

    async def _retrieve() -> list[str]:
        # A search string, not stored text — Voyage embeds the two differently.
        embeddings = await _embed_chunks(
            [question], input_type="query", config=config
        )
        query_vector = embeddings[0]

        pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(index_name_for(config))
        results = await asyncio.to_thread(
            index.query,
            vector=query_vector,
            top_k=_TOP_K,
            namespace=namespace,
            include_metadata=True,
        )
        return [
            (m.metadata or {}).get("text", "")
            for m in results.matches
            if (m.metadata or {}).get("text")
        ]

    try:
        # Bound retrieval so a slow embed/Pinecone call doesn't delay the first
        # streamed token past NFR-P2 (10s) — mirrors query_knowledge_base.
        return await asyncio.wait_for(_retrieve(), timeout=10.0)
    except TimeoutError:
        logger.warning("Ticket retrieval timed out for session=%s (>10s)", session_id)
        return []
    except Exception:
        logger.warning(
            "Ticket chunk retrieval failed for session=%s — answering without context",
            session_id,
        )
        return []


def _build_prompt(question: str, chunks: list[str]) -> str:
    """Build the grounded user prompt from retrieved chunks + the question."""
    context = (
        "\n\n".join(f"- {c}" for c in chunks)
        if chunks
        else "(no relevant ticket content found)"
    )
    return f"CONTEXT:\n{context}\n\nQUESTION: {question}"


async def stream_chat_answer(
    session_id: str,
    user_id: str,
    question: str,
    llm: LLMProvider,
    db: AsyncSession,
):
    """Stream a grounded answer as SSE and persist the question/answer pair.

    Yields SSE strings:
      data: {"type": "token", "content": "..."}\\n\\n
      data: {"type": "complete"}\\n\\n
      data: {"type": "error", "message": "..."}\\n\\n
    """
    # Persist the user message up-front so it survives an LLM failure mid-stream.
    db.add(ChatMessage(
        session_id=session_id, user_id=user_id, role="user", content=question
    ))
    await db.flush()

    # The session knows its project; the project knows how its vectors were
    # embedded and where they live. Resolved here rather than taken from the
    # request, because what matters is how this session was INDEXED — a value
    # the caller could not know and must not be able to override.
    config = await _vector_config_for_session(session_id, user_id, db)

    chunks = await _retrieve_ticket_chunks(user_id, session_id, question, config)
    prompt = _build_prompt(question, chunks)

    answer_parts: list[str] = []
    try:
        # Low temperature — the answer must stay grounded in the retrieved context.
        async for token in llm.generate_stream(prompt, _SYSTEM_PROMPT, temperature=0.2):
            if token:
                answer_parts.append(token)
                yield _sse({"type": "token", "content": token})
    except LLMProviderError as exc:
        yield _sse({"type": "error", "message": str(exc)})
        return
    except Exception:
        logger.exception("Chat streaming failed for session=%s", session_id)
        yield _sse({"type": "error", "message": "Failed to generate a response."})
        return

    answer = "".join(answer_parts)
    db.add(ChatMessage(
        session_id=session_id, user_id=user_id, role="assistant", content=answer
    ))
    await db.commit()

    yield _sse({"type": "complete"})
