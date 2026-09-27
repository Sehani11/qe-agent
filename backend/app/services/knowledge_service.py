"""Project knowledge base ingestion service.

Handles ingestion of external knowledge sources (Confluence, Jira) into the
Pinecone vector database, under one namespace per project.

Architecture rules enforced here:
- All Confluence HTTP calls go through confluence_service — never direct httpx here
- Pinecone namespaces come from `knowledge_namespace()` — never built inline,
  and never from settings.dev_user_id
- No business logic belongs in route handlers
- DB persistence happens after each page is successfully embedded
"""

import asyncio
import hashlib
import io
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator

import pinecone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge_source import KnowledgeSource
from app.schemas.knowledge import ConfluenceIngestRequest, JiraIngestRequest
from app.services import confluence_service, jira_service
from app.services.confluence_service import ConfluenceServiceError
from app.services.jira_service import JiraServiceError
from app.services.project_config_service import (
    confluence_credentials_for,
    jira_credentials_for,
    load_project,
    vector_config_for,
)
from app.services.vector_service import (
    EmbeddingsUnavailableError,
    _embed_chunks,
    chunk_text,
    index_name_for,
)

logger = logging.getLogger(__name__)

# Max chunks per embeddings request — keeps large documents under the OpenAI
# per-request input/token limits (Story 4.6).
_EMBED_BATCH_SIZE = 100


class DocumentIngestError(Exception):
    """Raised when an uploaded document cannot be parsed or has no usable text."""

    def __init__(self, message: str, code: str = "DOCUMENT_INGEST_FAILED"):
        super().__init__(message)
        self.message = message
        self.code = code


def knowledge_namespace(user_id: str, project_id: uuid.UUID | str | None) -> str:
    """The Pinecone namespace holding one project's knowledge base.

    Scoped per project so one project's verification cannot draw on another
    project's documents — which is most of the point of having projects.

    `project_id=None` returns the pre-projects namespace. That is not a
    convenience fallback: it is the only way to reach knowledge ingested before
    this change, and `scripts/migrate_knowledge_vectors.py` copies it forward.
    A caller that has a project must always pass it, or it will read an empty
    namespace once that copy has run.
    """
    if project_id is None:
        return f"{user_id}:knowledge"
    return f"{user_id}:{project_id}:knowledge"


def code_namespace(user_id: str, project_id: uuid.UUID | str | None) -> str:
    """The Pinecone namespace holding one project's indexed source code.

    A separate namespace from ``knowledge_namespace`` in the SAME index. The
    two hold different kinds of thing and are queried by different callers, but
    both are embedded by the project's one configured vendor and so share its
    dimension — which is what makes one index correct and two wasteful.

    It lives here rather than in ``code_index_service`` so the deletion paths
    below can reach it. They have to: code vectors are keyed per file path, so
    nothing that targets a source by ``source_ref`` can find them, and a code
    row removed without this would leave every one of its vectors behind.
    """
    if project_id is None:
        return f"{user_id}:code"
    return f"{user_id}:{project_id}:code"


def _sse(payload: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(payload)}\n\n"


async def _upsert_to_pinecone(
    source_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
    namespace: str,
    source: str = "confluence",
    title: str = "",
    url: str = "",
    config=None,
) -> None:
    """Upsert embedded chunks into Pinecone under the given namespace.

    `title` and `url` are stored in metadata (Story 4.4) so the RAG context
    display panel can render human-readable, clickable sources without a
    separate DB lookup.

    `config` is the project's resolved `VectorConfig` and MUST be the one the
    embeddings were produced with — writing a vendor's vectors into another
    vendor's index is what Pinecone rejects as a dimension mismatch. `None`
    falls back to the deployment settings.
    """
    if not settings.pinecone_api_key:
        return  # Skip in environments without Pinecone configured

    # Ids are deterministic (f"{source}_{source_id}_chunk_{i}"), so re-ingesting
    # a source that SHRANK would overwrite the head chunks but leave stale tail
    # chunks behind — retrieval would keep serving deleted content. Clear the
    # source's existing vectors first; a cleanup failure must not block indexing.
    try:
        await asyncio.to_thread(
            _delete_vectors_sync, namespace, source, source_id, config
        )
    except Exception:
        logger.warning(
            "Pre-upsert cleanup failed for %s_%s in %s — stale chunks may remain",
            source,
            source_id,
            namespace,
        )

    pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(index_name_for(config))

    vectors = [
        {
            "id": f"{source}_{source_id}_chunk_{i}",
            "values": embeddings[i],
            "metadata": {
                "text": chunk,
                "source_id": source_id,
                "source": source,
                "title": title,
                "url": url,
            },
        }
        for i, chunk in enumerate(chunks)
    ]

    await asyncio.to_thread(index.upsert, vectors=vectors, namespace=namespace)


# --- Hybrid-lite retrieval -------------------------------------------------
# Pure cosine similarity on prose embeddings is weak on exact identifiers: a
# query naming "PROJ-142" or "login_handler" can rank the chunk that literally
# contains that token below generic prose. Two cheap compensations, no new
# infrastructure:
#   1. over-fetch the vector query and re-rank by exact-identifier hits;
#   2. for explicitly named ticket keys, run one extra metadata-filtered query
#      (chunks carry source_id) whose hits are merged in front — an explicitly
#      named ticket is relevant by declaration, not by embedding distance.

_TICKET_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
_CODE_TOKEN_RE = re.compile(
    r"\b(?:"
    r"[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+"   # snake_case
    r"|[a-z]+(?:[A-Z][a-z0-9]+)+"             # camelCase
    r"|[A-Za-z_]\w*\(\)"                   # call()
    r")"
)

# Vector-query multiplier so the lexical re-rank has candidates beyond top_k.
_QUERY_OVERFETCH = 3


def _extract_identifiers(text: str) -> tuple[set[str], set[str]]:
    """Return (ticket_keys, code_tokens) literally present in the query text."""
    ticket_keys = set(_TICKET_KEY_RE.findall(text))
    code_tokens = {
        t[:-2] if t.endswith("()") else t for t in _CODE_TOKEN_RE.findall(text)
    } - ticket_keys
    return ticket_keys, code_tokens


def _lexical_hits(match, identifiers: set[str]) -> int:
    """Count query identifiers appearing verbatim (case-insensitive) in a chunk."""
    text = ((match.metadata or {}).get("text") or "").lower()
    return sum(1 for ident in identifiers if ident.lower() in text)


def _match_id(match) -> object:
    return getattr(match, "id", None) or id(match)


async def _query_matches_hybrid(
    index, namespace: str, vector: list[float], query_text: str, top_k: int
) -> list:
    """Vector search + identifier re-rank + exact ticket-key pull.

    Returns at most top_k Pinecone matches. The extra filtered query runs only
    when the query names ticket keys, so the common prose query costs exactly
    one Pinecone call as before (just with a larger top_k).
    """
    results = await asyncio.to_thread(
        index.query,
        vector=vector,
        top_k=top_k * _QUERY_OVERFETCH,
        namespace=namespace,
        include_metadata=True,
    )
    matches = [m for m in results.matches if _is_relevant(m)]

    ticket_keys, code_tokens = _extract_identifiers(query_text)
    identifiers = ticket_keys | code_tokens
    if identifiers:
        matches.sort(
            key=lambda m: (
                _lexical_hits(m, identifiers),
                getattr(m, "score", 0) or 0,
            ),
            reverse=True,
        )
    matches = matches[:top_k]

    if ticket_keys:
        exact_results = await asyncio.to_thread(
            index.query,
            vector=vector,
            top_k=top_k,
            namespace=namespace,
            include_metadata=True,
            filter={"source_id": {"$in": sorted(ticket_keys)}},
        )
        seen = {_match_id(m) for m in matches}
        # No relevance floor here — the user named the ticket explicitly.
        exact = [m for m in exact_results.matches if _match_id(m) not in seen]
        matches = (exact + matches)[:top_k]

    return matches


def _is_relevant(match) -> bool:
    """True if a Pinecone match clears the relevance floor (settings.rag_min_score).

    Pinecone always returns top-K matches regardless of how similar they are, so
    without this filter a scenario surfaces loosely-related or unrelated chunks.
    A non-numeric score (e.g. score unavailable) is not filtered.
    """
    score = getattr(match, "score", None)
    if not isinstance(score, int | float):
        return True
    return score >= settings.rag_min_score


SNIPPET_LIMIT = 300


def _snippet(text: str, limit: int = SNIPPET_LIMIT) -> str:
    """Excerpt a chunk for display and persistence, ending on a whole word.

    A hard slice ended mid-word, which reads in the UI as text that failed to
    load rather than as an excerpt. The ellipsis is the part that says the
    source continues; it is counted against `limit` so callers that size a
    column or a payload on that number still hold.
    """
    text = text.strip()
    if len(text) <= limit:
        return text

    head = text[: limit - 1]
    # Back up to the last space so the excerpt ends on a whole word. A chunk
    # with no space in its first `limit` characters (a URL, a minified blob)
    # has no boundary to find, so the hard cut is all that is available.
    boundary = head.rfind(" ")
    if boundary > 0:
        head = head[:boundary]
    return head.rstrip(" ,;:.—-") + "…"


def _chunk_from_match(match) -> dict:
    """Build a RAG context dict from a Pinecone match (Story 4.4).

    Reads title/url from metadata when present. For chunks ingested before
    Story 4.4 (metadata lacks title/url), degrades gracefully:
    - Jira: derive the browse URL from settings.jira_base_url + source_id
    - title: left empty; the frontend falls back to source_id
    """
    meta = match.metadata or {}
    source = meta.get("source", "")
    source_id = meta.get("source_id", "")
    url = meta.get("url", "")

    if not url and source == "jira" and settings.jira_base_url and source_id:
        url = f"{settings.jira_base_url.rstrip('/')}/browse/{source_id}"

    return {
        "source": source,
        "source_id": source_id,
        # snippet: what the UI shows and what is persisted on verdicts.
        "snippet": _snippet(meta.get("text", "")),
        # text: the full chunk, for LLM grounding — answering from a 300-char
        # snippet loses most of the retrieved evidence.
        "text": meta.get("text", ""),
        "title": meta.get("title", ""),
        "url": url,
    }


async def query_knowledge_base(
    user_id: str,
    query_text: str,
    top_k: int = 5,
    project_id: uuid.UUID | str | None = None,
    config=None,
) -> list[dict]:
    """Query the user's Pinecone knowledge namespace for relevant chunks.

    Returns list[dict] with keys: source, source_id, snippet.
    Returns [] on any failure — never raises (graceful degradation per NFR-R3).
    Enforces a 10-second total budget (NFR-P6) via asyncio.wait_for.

    `config` must be the same `VectorConfig` this project was INGESTED with:
    the query is embedded here, and a vector from a different model is not
    comparable to what is stored, so a mismatch returns nothing rather than
    failing. `None` falls back to the deployment settings.
    """
    if not settings.pinecone_api_key or not query_text.strip():
        return []

    namespace = knowledge_namespace(user_id, project_id)

    async def _retrieve() -> list[dict]:
        # A search string, not stored text — Voyage embeds the two differently.
        embeddings = await _embed_chunks(
            [query_text], input_type="query", config=config
        )
        query_vector = embeddings[0]

        pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(index_name_for(config))

        matches = await _query_matches_hybrid(
            index, namespace, query_vector, query_text, top_k
        )
        return [_chunk_from_match(m) for m in matches]

    try:
        return await asyncio.wait_for(_retrieve(), timeout=10.0)
    except TimeoutError:
        logger.warning(
            "RAG retrieval timed out for user=%s after 10s"
            " — proceeding without context",
            user_id,
        )
        return []
    except Exception:
        logger.warning(
            "RAG retrieval failed for user=%s — proceeding without context",
            user_id,
        )
        return []


async def query_knowledge_base_batch(
    user_id: str,
    query_texts: list[str],
    top_k: int = 5,
    project_id: uuid.UUID | str | None = None,
    config=None,
) -> list[list[dict]]:
    """Query the knowledge namespace for MANY texts with one embeddings request.

    Embeds all ``query_texts`` in a single ``_embed_chunks`` call (avoiding N
    separate embedding round-trips) and reuses one Pinecone client, then runs one
    query per text. Returns a list aligned with ``query_texts`` (each element the
    chunks for that text).

    Graceful (NFR-P6/R3): returns ``[]`` for a text on that query's failure and
    an all-empty result if embedding or the whole batch fails — never raises. A
    single query's failure is isolated (``return_exceptions=True``) so it doesn't
    discard the other texts' context.
    """
    if not settings.pinecone_api_key or not query_texts:
        return [[] for _ in query_texts]

    namespace = knowledge_namespace(user_id, project_id)

    async def _retrieve_all() -> list[list[dict]]:
        # Embed in bounded batches so one oversized request can't exceed the
        # embeddings token limit and zero out every scenario. Blank texts are
        # replaced with a single space so an empty input can't 400 the request
        # while keeping the result aligned 1:1 with query_texts.
        safe_texts = [t if t.strip() else " " for t in query_texts]
        embeddings: list[list[float]] = []
        for i in range(0, len(safe_texts), _EMBED_BATCH_SIZE):
            batch = safe_texts[i : i + _EMBED_BATCH_SIZE]
            embeddings.extend(
                await _embed_chunks(batch, input_type="query", config=config)
            )

        pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(index_name_for(config))

        async def _query_one(vector: list[float], text: str) -> list[dict]:
            matches = await _query_matches_hybrid(
                index, namespace, vector, text, top_k
            )
            return [_chunk_from_match(m) for m in matches]

        raw = await asyncio.gather(
            *(
                _query_one(v, t)
                for v, t in zip(embeddings, safe_texts, strict=True)
            ),
            return_exceptions=True,
        )
        return [r if isinstance(r, list) else [] for r in raw]

    try:
        result = await asyncio.wait_for(_retrieve_all(), timeout=10.0)
    except TimeoutError:
        logger.warning(
            "Batch RAG retrieval timed out for user=%s after 10s "
            "— proceeding without context",
            user_id,
        )
        return [[] for _ in query_texts]
    except Exception:
        logger.warning(
            "Batch RAG retrieval failed for user=%s — proceeding without context",
            user_id,
        )
        return [[] for _ in query_texts]

    # Guarantee the result is aligned 1:1 with query_texts even if the embeddings
    # backend ever returns an unexpected count — callers zip(strict=True) on this,
    # so a length mismatch would otherwise crash the verification stream.
    if len(result) != len(query_texts):
        logger.warning(
            "Batch RAG retrieval returned %d results for %d texts (user=%s) "
            "— padding to align",
            len(result),
            len(query_texts),
            user_id,
        )
        result = (result + [[] for _ in query_texts])[: len(query_texts)]
    return result


def _delete_vectors_sync(
    namespace: str, source_type: str, source_ref: str, config=None
) -> None:
    """Delete all vectors for one source from a namespace (runs in a thread).

    Vectors were upserted with ids ``f"{source}_{source_id}_chunk_{i}"``. Two
    deletion strategies cover both Pinecone index kinds:
      A) serverless — list ids by prefix, then delete by id (batched)
      B) pod        — metadata-filtered delete on {source, source_id}
    Strategy A is tried first; on any failure it falls back to B.

    `config` selects the index. Deleting from the wrong one is a quiet no-op
    that leaves the real vectors in place, so it must be the project's.
    """
    pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(index_name_for(config))
    prefix = f"{source_type}_{source_ref}_chunk_"

    try:
        ids: list[str] = []
        for page in index.list(prefix=prefix, namespace=namespace):
            # index.list yields pages (lists of ids); tolerate a bare-str yield.
            if isinstance(page, str):
                ids.append(page)
            else:
                ids.extend(page)
        for i in range(0, len(ids), 1000):
            index.delete(ids=ids[i : i + 1000], namespace=namespace)
        return
    except Exception as exc_list:
        logger.warning(
            "Prefix-list delete failed for %s (%s); trying metadata filter",
            prefix,
            exc_list,
        )

    index.delete(
        filter={"source": source_type, "source_id": source_ref},
        namespace=namespace,
    )


async def delete_source_vectors(
    user_id: str,
    source_type: str,
    source_ref: str | None,
    project_id: uuid.UUID | str | None = None,
    config=None,
) -> None:
    """Best-effort removal of a source's vectors from its project namespace.

    No-op when Pinecone is unconfigured or ``source_ref`` is unknown (rows
    ingested before Story 4.7). Never raises — a vector-store hiccup must not
    block removing the DB row; the failure is logged (may leave orphan vectors).
    """
    if not source_ref:
        # Legacy row (pre-Story-4.7) has no vector-id prefix — its chunks cannot
        # be targeted. Surface this so a "deleted" source doesn't silently keep
        # answering RAG/chat from vectors we couldn't remove.
        logger.warning(
            "Knowledge source for user=%s (%s) has no source_ref — its vectors "
            "cannot be targeted and may persist in the knowledge base.",
            user_id,
            source_type,
        )
        return
    if not settings.pinecone_api_key:
        return
    namespace = knowledge_namespace(user_id, project_id)
    try:
        await asyncio.to_thread(
            _delete_vectors_sync, namespace, source_type, source_ref, config
        )
    except Exception:
        logger.warning(
            "Vector deletion failed for %s/%s in %s — DB row still removed",
            source_type,
            source_ref,
            namespace,
        )


def _delete_namespace_sync(namespace: str, config=None) -> None:
    """Delete every vector in a namespace (runs in a thread).

    One call instead of per-source deletes. It also clears vectors that no
    per-source delete could target — legacy rows without a ``source_ref``, and
    orphans left behind by an earlier failed deletion — which is why "delete
    all" is not simply a loop over ``delete_source_vectors``.
    """
    pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(index_name_for(config))
    index.delete(delete_all=True, namespace=namespace)


async def delete_all_source_vectors(
    user_id: str, project_id: uuid.UUID | str | None = None, config=None
) -> None:
    """Best-effort wipe of the user's whole knowledge namespace.

    Never raises, matching ``delete_source_vectors``: a vector-store hiccup must
    not block removing the DB rows. Pinecone 404s a namespace that does not
    exist yet (nothing was ever ingested), which is a success for our purposes.
    """
    if not settings.pinecone_api_key:
        return
    namespace = knowledge_namespace(user_id, project_id)
    try:
        await asyncio.to_thread(_delete_namespace_sync, namespace, config)
    except Exception:
        logger.warning(
            "Namespace wipe failed for %s — DB rows still removed, vectors may persist",
            namespace,
            exc_info=True,
        )


async def delete_all_knowledge_sources(
    user_id: str, db: AsyncSession, project_id: uuid.UUID | str | None = None
) -> int:
    """Delete every knowledge source in one project. Returns the count.

    Both halves are scoped to the SAME project: the rows by ``project_id`` and
    the vectors by the namespace that id names. Deleting the rows of one project
    while wiping another's namespace would leave each project holding the other's
    debris — orphan vectors that still answer retrieval with no row left to
    remove them, and rows pointing at vectors that are already gone.

    Vectors go first (whole namespace, best-effort), then the rows — the same
    order as the single-source path, so a partial failure leaves rows that can
    be retried rather than rows pointing at vectors that are already gone.
    """
    result = await db.execute(
        select(KnowledgeSource).where(
            KnowledgeSource.user_id == user_id,
            KnowledgeSource.project_id == project_id,
        )
    )
    sources = list(result.scalars().all())
    if not sources:
        return 0

    await delete_all_source_vectors(user_id, project_id)
    # The code index lives in its own namespace, which the wipe above does not
    # touch. Removing its row without this leaves every code vector behind with
    # nothing left to delete it.
    if any(source.source_type == "code" for source in sources):
        await delete_code_index_vectors(user_id, project_id)
    for source in sources:
        await db.delete(source)
    await db.commit()
    return len(sources)


async def delete_code_index_vectors(
    user_id: str, project_id: uuid.UUID | str | None, config=None
) -> None:
    """Best-effort wipe of one project's indexed code.

    The whole namespace, not a per-source delete. Code vectors are keyed by
    file path (``code_{path}_chunk_{i}``) while the row that records the index
    is keyed by repository, so nothing that targets a ``source_ref`` can reach
    them — and a project has exactly one code index, which makes the namespace
    and the index the same thing.

    Never raises, matching the other deletion helpers: a vector-store hiccup
    must not block removing the DB row.
    """
    if not settings.pinecone_api_key:
        return
    namespace = code_namespace(user_id, project_id)
    try:
        await asyncio.to_thread(_delete_namespace_sync, namespace, config)
    except Exception:
        logger.warning(
            "Code-index wipe failed for %s — the row is still removed, so "
            "retrieval is off, but the vectors may persist",
            namespace,
            exc_info=True,
        )


async def delete_knowledge_source(source: KnowledgeSource, db: AsyncSession) -> None:
    """Delete an (already ownership-verified) knowledge source and its vectors.

    Removes the Pinecone vectors first (best-effort), then the DB row.

    The namespace comes from the ROW's own ``project_id``, never from a request
    parameter: that is the project the vectors were embedded into, and it is the
    only value that can find them. Omitting it targets the pre-projects
    namespace, where a project-scoped source has nothing — so the row would
    disappear from the list while its content kept answering retrieval.
    """
    if source.source_type == "code":
        # Its vectors are keyed per file path, so the per-source delete below
        # would match none of them and leave the whole index orphaned.
        await delete_code_index_vectors(source.user_id, source.project_id)
    else:
        await delete_source_vectors(
            source.user_id, source.source_type, source.source_ref, source.project_id
        )
    await db.delete(source)
    await db.commit()


async def ingest_confluence(
    user_id: str,
    request: ConfluenceIngestRequest,
    db: AsyncSession,
    config=None,
) -> AsyncGenerator[str, None]:
    """Stream SSE events while ingesting Confluence pages into the knowledge base.

    Yields SSE-formatted strings:
      data: {"type": "progress", "message": "...", "current": N, "total": M}\\n\\n
      data: {"type": "complete", "ingested_count": N}\\n\\n
      data: {"type": "error", "error": "CODE", "message": "..."}\\n\\n

    Args:
        user_id: Authenticated user ID from JWT — used as Pinecone namespace prefix.
        request: Validated ConfluenceIngestRequest with space_key or page_id.
        db: AsyncSession injected by FastAPI dependency.
    """
    namespace = knowledge_namespace(user_id, request.project_id)

    yield _sse({"type": "progress", "message": "Connecting to Confluence..."})

    # Resolved once, before the first fetch: every page in this run must come
    # from the same Confluence, or the batch would silently span two tenants.
    project = await load_project(db, user_id, request.project_id)
    confluence_creds = confluence_credentials_for(project)
    # The same project decides how its text is embedded and where it lands. An
    # explicit config from the caller wins, so a route that already resolved
    # one is not second-guessed here.
    config = config or vector_config_for(project)

    try:
        # page_refs wins: it is the explicit, newest way in. space_key and
        # page_id keep their prior precedence behind it so existing callers
        # behave exactly as before.
        if request.page_refs:
            page_ids = [
                confluence_service.extract_page_id(ref) for ref in request.page_refs
            ]
            # Deduplicate while preserving order — the same page pasted twice
            # would otherwise be embedded twice under the same vector ids.
            seen: set[str] = set()
            unique_ids = [
                pid for pid in page_ids if not (pid in seen or seen.add(pid))
            ]
            pages = [
                await confluence_service.fetch_page_by_id(pid, confluence_creds)
                for pid in unique_ids
            ]
        elif request.space_key:
            pages = await confluence_service.fetch_pages_from_space(
                request.space_key, confluence_creds
            )
        else:
            page = await confluence_service.fetch_page_by_id(
                request.page_id,  # type: ignore[arg-type]
                confluence_creds,
            )
            pages = [page]

    except ConfluenceServiceError as exc:
        yield _sse({"type": "error", "error": exc.code, "message": exc.message})
        return
    except Exception:
        yield _sse({
            "type": "error",
            "error": "KNOWLEDGE_INGEST_FAILED",
            "message": "Failed to fetch from Confluence. Please try again.",
        })
        return

    if not pages:
        yield _sse({"type": "complete", "ingested_count": 0})
        return

    total = len(pages)
    ingested_count = 0

    for i, page in enumerate(pages, start=1):
        yield _sse({
            "type": "progress",
            "message": f"Processing: {page.title}",
            "current": i,
            "total": total,
        })

        try:
            chunks = chunk_text(page.body)
            if not chunks:
                yield _sse({
                    "type": "progress",
                    "message": f"Skipped (no content): {page.title}",
                    "current": i,
                    "total": total,
                })
                continue

            embeddings = await _embed_chunks(chunks, config=config)
            await _upsert_to_pinecone(
                page.id,
                chunks,
                embeddings,
                namespace,
                source="confluence",
                title=page.title,
                url=page.url,
                config=config,
            )

            knowledge_source = KnowledgeSource(
                user_id=user_id,
                project_id=request.project_id,
                source_type="confluence",
                source_ref=page.id,
                source_url=page.url,
                title=page.title,
                page_count=1,
                ingestion_status="completed",
            )
            db.add(knowledge_source)
            await db.commit()

            ingested_count += 1

        except EmbeddingsUnavailableError as exc:
            yield _sse({"type": "error", "error": exc.code, "message": exc.message})
            return
        except Exception:
            logger.exception("Failed to embed/persist page '%s'", page.title)
            yield _sse({
                "type": "progress",
                "message": f"Failed to process: {page.title}",
                "current": i,
                "total": total,
            })
            db.add(KnowledgeSource(
                user_id=user_id,
                project_id=request.project_id,
                source_type="confluence",
                source_url=page.url,
                title=page.title,
                page_count=0,
                ingestion_status="failed",
            ))
            await db.commit()

    yield _sse({"type": "complete", "ingested_count": ingested_count})


async def ingest_jira(
    user_id: str,
    request: JiraIngestRequest,
    db: AsyncSession,
    config=None,
) -> AsyncGenerator[str, None]:
    """Stream SSE events while ingesting Jira project tickets into the knowledge base.

    Yields SSE-formatted strings:
      data: {"type": "progress", "message": "...", "current": N, "total": M}\\n\\n
      data: {"type": "complete", "ingested_count": N}\\n\\n
      data: {"type": "error", "error": "CODE", "message": "..."}\\n\\n

    Args:
        user_id: Authenticated user ID from JWT — used as Pinecone namespace prefix.
        request: Validated JiraIngestRequest with project_key and optional filters.
        db: AsyncSession injected by FastAPI dependency.
    """
    namespace = knowledge_namespace(user_id, request.project_id)

    yield _sse({"type": "progress", "message": "Connecting to Jira..."})

    # Resolved once, before the first fetch: every ticket in this run must come
    # from the same Jira, or the batch would silently span two tenants.
    project = await load_project(db, user_id, request.project_id)
    jira_creds = jira_credentials_for(project)
    config = config or vector_config_for(project)

    try:
        if request.ticket_refs:
            # fetch_ticket_content already accepts a URL or a bare key, so the
            # same parser the ingestion flow uses handles both here.
            tickets = []
            seen_keys: set[str] = set()
            for ref in request.ticket_refs:
                ticket = await jira_service.fetch_ticket_content(
                    ref, jira_creds
                )
                # The same ticket given as both a URL and a key resolves to one
                # id; embedding it twice would duplicate its chunks.
                if ticket.ticket_id in seen_keys:
                    continue
                seen_keys.add(ticket.ticket_id)
                tickets.append(ticket)
        else:
            tickets = await jira_service.fetch_tickets_from_project(
                request.project_key,  # type: ignore[arg-type]
                sprint=request.sprint,
                label=request.label,
                credentials=jira_creds,
            )
    except JiraServiceError as exc:
        yield _sse({"type": "error", "error": exc.code, "message": exc.message})
        return
    except Exception:
        yield _sse({
            "type": "error",
            "error": "KNOWLEDGE_INGEST_FAILED",
            "message": "Failed to fetch from Jira. Please try again.",
        })
        return

    if not tickets:
        yield _sse({"type": "complete", "ingested_count": 0})
        return

    total = len(tickets)
    ingested_count = 0

    for i, ticket in enumerate(tickets, start=1):
        yield _sse({
            "type": "progress",
            "message": f"Processing: {ticket.ticket_id} — {ticket.summary}",
            "current": i,
            "total": total,
        })

        try:
            body = (
                f"Summary: {ticket.summary}\n\n"
                f"Description: {ticket.description}\n\n"
                f"Acceptance Criteria: {ticket.acceptance_criteria}"
            )
            chunks = chunk_text(body)
            if not chunks:
                yield _sse({
                    "type": "progress",
                    "message": f"Skipped (no content): {ticket.ticket_id}",
                    "current": i,
                    "total": total,
                })
                continue

            embeddings = await _embed_chunks(chunks, config=config)
            source_url = (
                f"{settings.jira_base_url.rstrip('/')}/browse/{ticket.ticket_id}"
            )
            await _upsert_to_pinecone(
                ticket.ticket_id,
                chunks,
                embeddings,
                namespace,
                source="jira",
                title=ticket.summary,
                url=source_url,
                config=config,
            )
            db.add(KnowledgeSource(
                user_id=user_id,
                project_id=request.project_id,
                source_type="jira",
                source_ref=ticket.ticket_id,
                source_url=source_url,
                title=ticket.summary,
                page_count=1,
                ingestion_status="completed",
            ))
            await db.commit()
            ingested_count += 1

        except EmbeddingsUnavailableError as exc:
            yield _sse({"type": "error", "error": exc.code, "message": exc.message})
            return
        except Exception:
            logger.exception(
                "Failed to embed/persist ticket '%s'", ticket.ticket_id
            )
            yield _sse({
                "type": "progress",
                "message": f"Failed to process: {ticket.ticket_id}",
                "current": i,
                "total": total,
            })
            db.add(KnowledgeSource(
                user_id=user_id,
                project_id=request.project_id,
                source_type="jira",
                source_url=(
                    f"{settings.jira_base_url.rstrip('/')}"
                    f"/browse/{ticket.ticket_id}"
                ),
                title=ticket.summary,
                page_count=0,
                ingestion_status="failed",
            ))
            await db.commit()

    yield _sse({"type": "complete", "ingested_count": ingested_count})


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from a PDF byte string (Story 4.6). Runs sync — offload
    with asyncio.to_thread. Raises DocumentIngestError on a corrupt file."""
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise DocumentIngestError(
            "Could not read the PDF. It may be corrupt or password-protected."
        ) from exc
    return "\n".join(parts).strip()


def _extract_docx_text(data: bytes) -> str:
    """Extract text from a DOCX byte string (Story 4.6). Runs sync — offload
    with asyncio.to_thread. Raises DocumentIngestError on a corrupt file."""
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs]
    except Exception as exc:
        raise DocumentIngestError(
            "Could not read the DOCX file. It may be corrupt."
        ) from exc
    return "\n".join(parts).strip()


async def ingest_document(
    user_id: str,
    filename: str,
    data: bytes,
    db: AsyncSession,
    project_id: uuid.UUID | str | None = None,
    config=None,
) -> dict:
    """Extract, chunk, embed and store an uploaded PDF/DOCX in the knowledge base.

    Returns {"ingested_count", "chunk_count", "title"}.
    Raises DocumentIngestError for unsupported types or empty/unreadable docs;
    records a knowledge_sources row (status completed|failed).
    """
    # Same rule as the other two ingest paths: the project owning the namespace
    # also decides how its text is embedded and which index holds it.
    if config is None:
        config = vector_config_for(await load_project(db, user_id, project_id))

    lower = filename.lower()
    if lower.endswith(".pdf"):
        text = await asyncio.to_thread(_extract_pdf_text, data)
    elif lower.endswith(".docx"):
        text = await asyncio.to_thread(_extract_docx_text, data)
    else:
        raise DocumentIngestError(
            "Unsupported file type. Upload a .pdf or .docx file.",
            code="UNSUPPORTED_FILE_TYPE",
        )

    chunks = chunk_text(text)
    if not chunks:
        # Track the failed attempt so the user sees it in the sources list.
        db.add(KnowledgeSource(
            user_id=user_id,
            project_id=project_id,
            source_type="document",
            source_url=None,
            title=filename,
            page_count=0,
            ingestion_status="failed",
        ))
        await db.commit()
        raise DocumentIngestError(
            "No extractable text found. Is this a scanned/image-only document?",
            code="NO_EXTRACTABLE_TEXT",
        )

    namespace = knowledge_namespace(user_id, project_id)
    # Filename-keyed, so re-uploading design.docx REPLACES the previous version
    # instead of duplicating its chunks in retrieval: the deterministic id makes
    # _upsert_to_pinecone's pre-clean remove the old vectors, and the
    # knowledge_sources row below is updated rather than re-inserted.
    source_id = f"doc-{hashlib.sha256(filename.strip().lower().encode()).hexdigest()[:16]}"

    try:
        # Embed in batches so large documents stay under the OpenAI
        # per-request input/token limits.
        embeddings: list[list[float]] = []
        for i in range(0, len(chunks), _EMBED_BATCH_SIZE):
            batch = chunks[i:i + _EMBED_BATCH_SIZE]
            embeddings.extend(await _embed_chunks(batch, config=config))
        await _upsert_to_pinecone(
            source_id,
            chunks,
            embeddings,
            namespace,
            source="document",
            title=filename,
            url="",
            config=config,
        )
    except EmbeddingsUnavailableError as exc:
        db.add(KnowledgeSource(
            user_id=user_id,
            project_id=project_id,
            source_type="document",
            source_url=None,
            title=filename,
            page_count=0,
            ingestion_status="failed",
        ))
        await db.commit()
        raise DocumentIngestError(exc.message, code=exc.code) from exc
    except Exception as exc:
        # Record the failed attempt and surface a clean error (not a 500).
        db.add(KnowledgeSource(
            user_id=user_id,
            project_id=project_id,
            source_type="document",
            source_url=None,
            title=filename,
            page_count=0,
            ingestion_status="failed",
        ))
        await db.commit()
        raise DocumentIngestError(
            "Failed to index the document. Please try again.",
            code="DOCUMENT_INDEX_FAILED",
        ) from exc

    existing = (
        await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.user_id == user_id,
                KnowledgeSource.source_type == "document",
                KnowledgeSource.source_ref == source_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.title = filename
        existing.page_count = len(chunks)
        existing.ingestion_status = "completed"
    else:
        db.add(KnowledgeSource(
            user_id=user_id,
            project_id=project_id,
            source_type="document",
            source_ref=source_id,
            source_url=None,
            title=filename,
            page_count=len(chunks),
            ingestion_status="completed",
        ))
    await db.commit()

    return {
        "ingested_count": 1,
        "chunk_count": len(chunks),
        "title": filename,
    }
