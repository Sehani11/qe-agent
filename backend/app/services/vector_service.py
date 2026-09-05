"""Vector embedding and Pinecone indexing service.

Owns the canonical ``_embed_chunks`` used by every RAG surface (knowledge base,
ticket chat, verification context) so the embedding model can never drift
between the write path and the read path — a mismatch would silently break
retrieval.
"""

import asyncio
import logging
import re
from typing import List

import httpx
import pinecone

from app.core.config import settings
from app.services.llm.factory import api_key_for

logger = logging.getLogger(__name__)

# One model for the whole system, chosen by EMBEDDING_PROVIDER. Which vendor is
# selected is a deployment decision, never a per-request one — see the setting's
# note in config.py for why tying it to the chat picker breaks retrieval.
_EMBEDDING_MODELS = {
    "openai": "text-embedding-3-small",
    "voyage": "voyage-4",
}

#: The Pinecone index must be created at the width its provider emits. Listed
#: here so the mismatch is findable: an index built for one provider cannot
#: hold the other's vectors, and Pinecone rejects the upsert outright.
_EMBEDDING_DIMENSIONS = {
    "openai": 1536,
    "voyage": 1024,
}

_DEFAULT_EMBEDDING_PROVIDER = "openai"

#: Env var to set, per provider, named in the "not configured" error.
_EMBEDDING_KEY_NAMES = {
    "openai": "OPENAI_API_KEY",
    "voyage": "VOYAGE_API_KEY",
}


def normalise_provider(configured: str | None) -> str:
    """Coerce a configured vendor name to one this module can serve.

    An unrecognised value falls back to the default rather than raising: a typo
    in one project's setting should not take down every RAG surface, and the
    fallback is the vendor the index was most likely built with anyway.
    """
    name = (configured or "").strip().lower()
    if name not in _EMBEDDING_MODELS:
        if name:
            logger.warning(
                "Unknown embedding provider %r; falling back to %r. Valid: %s",
                name,
                _DEFAULT_EMBEDDING_PROVIDER,
                ", ".join(sorted(_EMBEDDING_MODELS)),
            )
        return _DEFAULT_EMBEDDING_PROVIDER
    return name


def model_for_provider(provider: str) -> str:
    """The model name a vendor will be asked for."""
    return _EMBEDDING_MODELS[normalise_provider(provider)]


def api_key_for_provider(provider: str) -> str:
    """The credential for one embedding vendor, never another's.

    Always from the environment, never the project: an embedding key is a
    billing credential for the deployment.
    """
    if normalise_provider(provider) == "voyage":
        return (settings.voyage_api_key or "").strip()
    return api_key_for("openai")


def embedding_provider() -> str:
    """The DEPLOYMENT-wide embedding vendor.

    The fallback for callers with no project in hand. A request that knows its
    project should resolve a `VectorConfig` instead — see
    `project_config_service.vector_config_for`.
    """
    return normalise_provider(settings.embedding_provider)


def embedding_model() -> str:
    """The model name the deployment-wide provider will be asked for."""
    return model_for_provider(embedding_provider())


def _embedding_api_key(provider: str) -> str:
    """Backwards-compatible alias for `api_key_for_provider`."""
    return api_key_for_provider(provider)


def _resolved(config) -> tuple[str, str, str, str]:
    """(provider, model, api_key, index_name) for a config, or the env default.

    ``config`` is a `project_config_service.VectorConfig`, kept untyped here to
    avoid an import cycle — that module imports this one for the primitives
    above. ``None`` means "no project in hand", which resolves to the
    deployment settings, so every pre-existing caller keeps its old behaviour
    without being changed.
    """
    if config is not None:
        return (config.provider, config.model, config.api_key, config.index_name)
    provider = embedding_provider()
    return (
        provider,
        model_for_provider(provider),
        api_key_for_provider(provider),
        settings.pinecone_index_name,
    )


def index_name_for(config=None) -> str:
    """The Pinecone index a call should read from or write to."""
    return _resolved(config)[3]


class VectorServiceError(Exception):
    """Custom error for vector embedding/indexing failures."""

    def __init__(self, message: str, code: str = "VECTOR_INDEX_FAILED"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class EmbeddingsUnavailableError(VectorServiceError):
    """Raised when no embedding backend is configured.

    Replaces the old random-vector fallback, which silently poisoned the index:
    garbage vectors index fine and retrieval then returns arbitrary chunks that
    are presented to the LLM as real project context.
    """

    def __init__(self, provider: str | None = None) -> None:
        name = provider or _DEFAULT_EMBEDDING_PROVIDER
        key = _EMBEDDING_KEY_NAMES.get(name, "OPENAI_API_KEY")
        super().__init__(
            f"Embeddings are not configured. Knowledge features require a "
            f"{name} key — set {key} (or change EMBEDDING_PROVIDER).",
            code="EMBEDDINGS_UNAVAILABLE",
        )


def embeddings_available(config=None) -> bool:
    """True when the embedding vendor for this call has a credential.

    Deliberately independent of which CHAT provider a request selected. An
    index is built with one embedding model and must stay consistent, so the
    vendor is a project (or deployment) setting, not a per-request choice:
    gating this on `llm_provider` meant that choosing Claude for chat silently
    emptied every RAG surface — retrieval returned no context and answers came
    back ungrounded, with nothing on screen to say why.
    """
    return bool(_resolved(config)[2])


async def _embed_chunks(
    chunks: list[str], input_type: str = "document", config=None
) -> list[list[float]]:
    """Generate embeddings for a list of text chunks.

    ``input_type`` is ``"document"`` for text being stored and ``"query"`` for
    text being searched with. Voyage embeds the two asymmetrically — it prepends
    a different instruction per type — and its docs are explicit that omitting
    it costs retrieval quality. OpenAI has no such parameter and ignores it, so
    the argument is safe to pass on every path. The default is ``"document"``
    because storing is the common case and the riskier one to get wrong: a
    mis-typed stored vector is wrong for the life of the index, while a
    mis-typed query is wrong only for that one call.

    ``config`` is the project's resolved `VectorConfig`; ``None`` falls back to
    the deployment settings. It must be the SAME config the matching Pinecone
    call uses — a vector embedded by one vendor and written to another vendor's
    index is either rejected outright or, worse, accepted and never comparable.

    Raises EmbeddingsUnavailableError when no backend is configured — callers
    on read paths degrade to "no context", write paths surface a clear error.
    """
    provider, model, api_key, _index = _resolved(config)
    if not api_key:
        raise EmbeddingsUnavailableError(provider)

    if provider == "voyage":
        url = "https://api.voyageai.com/v1/embeddings"
        payload: dict = {
            "input": chunks,
            "model": model,
            "input_type": input_type,
        }
    else:
        url = "https://api.openai.com/v1/embeddings"
        payload = {"input": chunks, "model": model}

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        res.raise_for_status()
        data = res.json()
        # Sort by `index` — both vendors return an `index` per item precisely
        # because array order is not guaranteed to match input order. Relying
        # on position would silently misalign batched per-scenario retrieval
        # (each vector paired with the wrong text).
        items = sorted(data["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Split text into ~chunk_size-word chunks on sentence boundaries with overlap."""
    if not text:
        return []

    # Split by naive sentence boundaries to preserve semantic domains better than pure split()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = []
    current_length = 0

    for sentence in sentences:
        words_in_sentence = len(sentence.split())
        if current_length + words_in_sentence > chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            # Keep the last few sentences for overlap (~overlap words)
            overlap_words = 0
            overlap_chunk = []
            for s in reversed(current_chunk):
                s_len = len(s.split())
                if overlap_words + s_len > overlap and overlap_chunk:
                    break
                overlap_chunk.insert(0, s)
                overlap_words += s_len
            current_chunk = overlap_chunk
            current_length = overlap_words

        current_chunk.append(sentence)
        current_length += words_in_sentence

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


async def embed_and_index_ticket(
    session_id: str,
    ticket_id: str,
    summary: str,
    description: str,
    acceptance_criteria: str,
    user_id: str,
    config=None,
) -> None:
    """Chunk and embed ticket content into Pinecone under the per-user session namespace.

    Namespace is ``{user_id}:{session_id}`` so the RAG chat (Story 5.1) can
    retrieve ticket chunks scoped to the authenticated owner (NFR-S4).

    ``config`` is the project's resolved `VectorConfig` — the same one the chat
    read path must use, or the question is embedded by a different model than
    the answer was stored with. ``None`` falls back to the deployment settings.

    Degrades rather than failing ingestion: with Pinecone or embeddings
    unconfigured the ticket is still ingested, only the chat loses retrieval
    context. A real indexing failure still raises so the user learns their
    session has no chat context.
    """
    content = (
        f"Ticket: {ticket_id}\nSummary: {summary}\nDescription: {description}\n"
        f"Acceptance Criteria: {acceptance_criteria}"
    )
    chunks = chunk_text(content)

    if not settings.pinecone_api_key or not chunks:
        return

    try:
        vectors_values = await _embed_chunks(chunks, config=config)
    except EmbeddingsUnavailableError:
        logger.warning(
            "Skipping ticket indexing for session=%s — embeddings not configured",
            session_id,
        )
        return
    except Exception:
        logger.exception("Embedding failed for session=%s", session_id)
        raise VectorServiceError("Failed to embed ticket content for indexing.")

    vectors = [
        {
            "id": f"chunk_{i}",
            "values": values,
            "metadata": {"text": chunks[i], "ticket_id": ticket_id},
        }
        for i, values in enumerate(vectors_values)
    ]

    try:
        pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(index_name_for(config))
        namespace = f"{user_id}:{session_id}"
        # Avoid blocking event loop for synchronous library call
        await asyncio.to_thread(index.upsert, vectors=vectors, namespace=namespace)
    except Exception:
        # Log the real cause server-side; keep the client-facing message generic.
        logger.exception("Pinecone indexing failed for session=%s", session_id)
        raise VectorServiceError(
            "Failed to connect and index to Pinecone cluster properly."
        )
