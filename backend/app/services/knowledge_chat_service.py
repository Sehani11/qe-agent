"""Project knowledge-base RAG chat service.

Answers project-wide natural-language questions grounded in the user's ingested
knowledge base (Confluence pages, Jira workspace tickets, uploaded documents),
retrieved from the Pinecone namespace ``{user_id}:knowledge`` via
``knowledge_service.query_knowledge_base``.

Contrast with ``chat_service`` (Story 5.1), which is scoped to a single ticket's
``{user_id}:{session_id}`` namespace. This service is user-scoped, session-less,
and therefore stateless — no chat history is persisted (there is no session to
anchor it to).

Architecture rules enforced here:
- LLM access only via the LLMProvider interface (never the SDK directly)
- Retrieval only via knowledge_service (never a direct Pinecone query here)
- No business logic in the route handler
"""

import json
import logging
import uuid

from app.services.knowledge_service import query_knowledge_base
from app.services.llm.provider import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

_TOP_K = 5

_SYSTEM_PROMPT = """\
You are a helpful assistant answering questions about a software project, using
its knowledge base (Confluence documentation, related Jira tickets, and uploaded
project documents).

Answer using ONLY the information in the CONTEXT below. If the CONTEXT does not
contain the answer, say that the project knowledge base does not cover it — do
NOT use outside knowledge, do NOT guess, and do NOT reference anything not in the
CONTEXT. Prefer concise, direct answers and cite source titles when helpful."""


def _sse(payload: dict) -> str:
    """Serialize a dict as an SSE data line."""
    return f"data: {json.dumps(payload)}\n\n"


def _build_prompt(question: str, chunks: list[dict]) -> str:
    """Build the grounded user prompt from retrieved knowledge chunks."""
    if chunks:
        lines = []
        for c in chunks:
            label = c.get("title") or c.get("source_id") or c.get("source") or "source"
            # Ground in the full chunk; the snippet is for the sources display.
            lines.append(f"- ({label}) {c.get('text') or c.get('snippet', '')}")
        context = "\n".join(lines)
    else:
        context = "(no relevant project knowledge found)"
    return f"CONTEXT:\n{context}\n\nQUESTION: {question}"


async def stream_knowledge_answer(
    user_id: str,
    question: str,
    llm: LLMProvider,
    project_id: uuid.UUID | str | None = None,
    config=None,
):
    """Stream a knowledge-base-grounded answer as SSE.

    Retrieves the top-K relevant chunks from ``{user_id}:knowledge``, grounds the
    LLM strictly in them, streams the answer, then emits the sources that were
    used (so the UI can cite them). Stateless — nothing is persisted.

    Yields SSE strings:
      data: {"type": "token", "content": "..."}\\n\\n
      data: {"type": "sources", "sources": [{source, title, url, snippet, ...}]}\\n\\n
      data: {"type": "complete"}\\n\\n
      data: {"type": "error", "message": "..."}\\n\\n
    """
    # Graceful: query_knowledge_base never raises — returns [] on any failure.
    chunks = await query_knowledge_base(
        user_id, question, top_k=_TOP_K, project_id=project_id, config=config
    )
    prompt = _build_prompt(question, chunks)

    try:
        # Low temperature — the answer must stay grounded in the retrieved context.
        async for token in llm.generate_stream(prompt, _SYSTEM_PROMPT, temperature=0.2):
            if token:
                yield _sse({"type": "token", "content": token})
    except LLMProviderError as exc:
        yield _sse({"type": "error", "message": str(exc)})
        return
    except Exception:
        logger.exception("Knowledge chat streaming failed for user=%s", user_id)
        yield _sse({"type": "error", "message": "Failed to generate a response."})
        return

    # Emit the retrieved sources after the answer so the UI can render citations.
    yield _sse({"type": "sources", "sources": chunks})
    yield _sse({"type": "complete"})
