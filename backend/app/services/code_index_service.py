"""Semantic index over a repository's source, used to find candidate files.

What this is for: agentic verification finds code by exploring. ``search_code``
returns nothing on a private repository (the normal case), so the agent walks
directories until it has a path worth reading — and every one of those rounds
replays the whole conversation before it. This module lets the agent be *told*
which files to open.

The rule that governs the whole design:

    **Retrieved chunks are a pointer to where to look. They are never the
    evidence a verdict rests on.**

Vector search cannot prove absence. It returns the top-k most similar chunks
and never returns "this does not exist". A ``fail`` verdict requires positive
evidence that behaviour is *not* in the code, and the verification prompt goes
to some length to protect that — the ``inconclusive`` status, the hedge
detector, the "a file you did not read is NOT evidence of absence" rule.
Feeding retrieved snippets in as evidence would let the model conclude "not
implemented" from a sample it cannot know is complete.

So ``find_candidate_files`` returns **paths and line ranges, never chunk
text**. The agent still calls ``get_file_contents`` and still judges what it
read live. Three further reasons retrieval cannot stand alone: a call chain
(page to hook to API client to route handler) is not a similarity
neighbourhood; cosine similarity on embeddings ranks the chunk literally
containing ``PROJ-142`` below generic prose; and an index is a snapshot while
the loop reads at a pinned ref.
"""

import asyncio
import io
import json
import logging
import tarfile
import uuid
from collections.abc import AsyncGenerator
from time import perf_counter

import httpx
import pinecone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge_source import KnowledgeSource
from app.services import github_tools
from app.services.code_chunking import chunk_code
from app.services.github_service import _auth_headers, _github_get
from app.services.knowledge_service import (
    _EMBED_BATCH_SIZE,
    _query_matches_hybrid,
    _upsert_to_pinecone,
    code_namespace,
)
from app.services.vector_service import (
    EmbeddingsUnavailableError,
    _embed_chunks,
    index_name_for,
)

logger = logging.getLogger(__name__)

#: Extensions worth indexing. Everything else in a repository — lockfiles,
#: images, generated output — is noise that would crowd real source out of the
#: top-k and cost embeddings to store.
_SOURCE_EXTENSIONS = frozenset(
    {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb",
        ".cs", ".php", ".rs", ".kt", ".swift", ".vue", ".svelte", ".scala",
    }
)

#: Files above this are generated, vendored or data — never the implementation
#: a scenario is about, and each one would produce hundreds of chunks.
_MAX_FILE_BYTES = 200 * 1024

#: The whole repository arrives as one tarball. Guards against pointing this at
#: something enormous and buffering it all in memory.
_MAX_TARBALL_BYTES = 100 * 1024 * 1024

#: Chunks per Pinecone upsert. Matches the embed batch so the two stay in step.
_UPSERT_BATCH_SIZE = 100

#: Candidate files offered per scenario. Small on purpose — this block is a
#: hint, and a long list of maybes is the agent's whole round budget.
_DEFAULT_TOP_K = 8

#: Total budget for embedding every scenario and querying for all of them.
#:
#: Spent once per RUN, not per scenario, against a verification that takes
#: minutes — so a few seconds here is cheap if it earns its keep, and the
#: fallback costs only what was spent before giving up. It is not generous
#: enough to hide a real problem: the phase timings are logged either way.
_RETRIEVAL_BUDGET_SECONDS = 20.0

_GITHUB_API_BASE = "https://api.github.com"


#: Re-exported so callers of this module need not know the namespace helper
#: lives next to ``knowledge_namespace`` — it is there so the knowledge-base
#: deletion paths can reach it without importing this module.
__all__ = [
    "code_namespace",
    "find_candidate_files",
    "format_candidate_block",
    "get_code_index",
    "index_repository",
    "resolve_commit_sha",
]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _is_source_path(path: str) -> bool:
    dot = path.rfind(".")
    return dot != -1 and path[dot:].lower() in _SOURCE_EXTENSIONS


async def resolve_commit_sha(repo: str, ref: str, pat: str) -> str:
    """The concrete commit a ref points at, or "" if it cannot be resolved.

    Indexing "main" and recording "main" says nothing — the branch moves and
    the record cannot tell a fresh index from a month-old one. Resolving to a
    SHA is what makes "indexed at" a fact and lets Step 5 detect staleness.
    """
    url = f"{_GITHUB_API_BASE}/repos/{repo}/commits/{ref}"
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await _github_get(client, url, _auth_headers(pat))
            if not resp.is_success:
                logger.info(
                    "Could not resolve %s@%s to a SHA (HTTP %s)",
                    repo,
                    ref,
                    resp.status_code,
                )
                return ""
            sha = resp.json().get("sha")
            return sha if isinstance(sha, str) else ""
    except Exception:
        logger.warning("Commit resolution failed for %s@%s", repo, ref, exc_info=True)
        return ""


def _extract_tarball(data: bytes, wanted: set[str]) -> dict[str, str]:
    """Pull the wanted paths out of a repo tarball. Runs sync — offload it.

    GitHub wraps the archive in a single top-level directory named for the
    repo and commit, which is stripped so keys match repository-relative paths.

    Anything undecodable or unreadable is skipped rather than raised: one
    binary file with a source extension must not fail a whole repository.
    """
    found: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or member.size > _MAX_FILE_BYTES:
                continue
            _, _, relative = member.name.partition("/")
            if relative not in wanted:
                continue
            try:
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                found[relative] = handle.read().decode("utf-8")
            except (UnicodeDecodeError, OSError, tarfile.TarError):
                continue
    return found


async def _fetch_repo_files(
    repo: str, sha: str, pat: str, paths: list[str]
) -> dict[str, str]:
    """Read every wanted file, preferring one tarball over thousands of reads.

    A large repository is several thousand files. Fetching them individually
    burns the GitHub rate limit, takes minutes, and runs each one through
    ``get_file_contents``' 40 000-character read window — which would silently
    index only the head of any larger file unless every one were paged through.
    The archive endpoint has none of those problems.

    Falls back to per-file reads when the archive cannot be had, because a
    slower index is better than no index.
    """
    url = f"{_GITHUB_API_BASE}/repos/{repo}/tarball/{sha}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            resp = await client.get(url, headers=_auth_headers(pat))
            if resp.is_success and len(resp.content) <= _MAX_TARBALL_BYTES:
                return await asyncio.to_thread(
                    _extract_tarball, resp.content, set(paths)
                )
            logger.info(
                "Tarball unavailable for %s@%s (HTTP %s); falling back to "
                "per-file reads",
                repo,
                sha,
                resp.status_code,
            )
    except Exception:
        logger.warning(
            "Tarball fetch failed for %s@%s; falling back to per-file reads",
            repo,
            sha,
            exc_info=True,
        )

    contents: dict[str, str] = {}
    for path in paths:
        try:
            contents[path] = await github_tools.get_file_contents(
                repo=repo, path=path, pat=pat, ref=sha
            )
        except Exception:
            logger.debug("Skipping unreadable file %s", path)
    return contents


async def index_repository(
    user_id: str,
    project_id: uuid.UUID | str | None,
    repo: str,
    ref: str,
    pat: str,
    db: AsyncSession,
    config=None,
) -> AsyncGenerator[str, None]:
    """Embed a repository's source into the project's ``:code`` namespace.

    Streams SSE progress the way ``knowledge_service.ingest_confluence`` does:
      data: {"type": "progress", "message": "...", "current": N, "total": M}
      data: {"type": "complete", "indexed_files": N, "chunks": M, "sha": "..."}
      data: {"type": "error", "error": "CODE", "message": "..."}

    ``config`` must be the project's ``VectorConfig`` — the same one
    ``find_candidate_files`` will query with. A vector embedded by one vendor
    is not comparable to one stored by another, so a mismatch here produces an
    index that silently returns nothing.
    """
    if not settings.pinecone_api_key:
        yield _sse(
            {
                "type": "error",
                "error": "PINECONE_NOT_CONFIGURED",
                "message": "Code indexing needs Pinecone. Set PINECONE_API_KEY.",
            }
        )
        return

    namespace = code_namespace(user_id, project_id)

    yield _sse({"type": "progress", "message": f"Resolving {repo}@{ref}..."})
    sha = await resolve_commit_sha(repo, ref, pat)
    if not sha:
        yield _sse(
            {
                "type": "error",
                "error": "REF_NOT_FOUND",
                "message": (
                    f"Could not resolve '{ref}' in {repo}. Check the branch "
                    "name and that the token can read this repository."
                ),
            }
        )
        return

    yield _sse({"type": "progress", "message": "Listing repository files..."})
    tree = await github_tools.get_repo_tree(repo, pat, sha)
    paths = [path for path in tree if _is_source_path(path)]
    if not paths:
        yield _sse(
            {
                "type": "error",
                "error": "NO_SOURCE_FILES",
                "message": (
                    f"No indexable source files found in {repo}. The tree may "
                    "be unavailable, or the repository may use languages this "
                    "index does not cover."
                ),
            }
        )
        return

    yield _sse(
        {
            "type": "progress",
            "message": f"Downloading {len(paths)} source files...",
            "current": 0,
            "total": len(paths),
        }
    )
    contents = await _fetch_repo_files(repo, sha, pat, paths)
    if not contents:
        yield _sse(
            {
                "type": "error",
                "error": "FETCH_FAILED",
                "message": f"Could not read any source files from {repo}@{sha[:7]}.",
            }
        )
        return

    # Chunked in one pass so the embed batches below are full regardless of how
    # small individual files are — one request per 100 chunks rather than per
    # file, which for a few thousand small files is the difference between one
    # minute and twenty.
    chunks: list[dict] = []
    for path in paths:
        text = contents.get(path)
        if text:
            chunks.extend(chunk_code(text, path))

    if not chunks:
        yield _sse(
            {
                "type": "error",
                "error": "NO_CONTENT",
                "message": "Source files were found but produced no indexable text.",
            }
        )
        return

    total = len(chunks)
    indexed = 0
    try:
        for start in range(0, total, _EMBED_BATCH_SIZE):
            batch = chunks[start : start + _EMBED_BATCH_SIZE]
            embeddings = await _embed_chunks(
                [chunk["text"] for chunk in batch],
                input_type="document",
                config=config,
            )
            await _upsert_code_chunks(batch, embeddings, namespace, config)
            indexed += len(batch)
            yield _sse(
                {
                    "type": "progress",
                    "message": f"Embedded {indexed} of {total} chunks",
                    "current": indexed,
                    "total": total,
                }
            )
    except EmbeddingsUnavailableError as exc:
        yield _sse({"type": "error", "error": exc.code, "message": exc.message})
        return
    except Exception:
        logger.exception("Code indexing failed for %s@%s", repo, sha)
        yield _sse(
            {
                "type": "error",
                "error": "CODE_INDEX_FAILED",
                "message": "Failed to index the repository. Please try again.",
            }
        )
        return

    await _record_index(db, user_id, project_id, repo, sha, len(contents))

    yield _sse(
        {
            "type": "complete",
            "indexed_files": len(contents),
            "chunks": total,
            "sha": sha,
        }
    )


async def _upsert_code_chunks(
    batch: list[dict], embeddings: list[list[float]], namespace: str, config
) -> None:
    """Write one batch of code chunks, grouped so each file is upserted once.

    ``_upsert_to_pinecone`` deletes a source's existing vectors before writing,
    which is what stops a file that shrank leaving stale tail chunks behind.
    That delete is keyed on ``source_id``, so all of a file's chunks have to go
    in one call — splitting a file across two calls would have the second call
    delete what the first just wrote.
    """
    by_path: dict[str, list[tuple[dict, list[float]]]] = {}
    for chunk, embedding in zip(batch, embeddings, strict=True):
        by_path.setdefault(chunk["path"], []).append((chunk, embedding))

    for path, items in by_path.items():
        await _upsert_to_pinecone(
            path,
            [chunk["text"] for chunk, _ in items],
            [embedding for _, embedding in items],
            namespace,
            source="code",
            # The line range travels in the title, which is the only free-text
            # metadata field _upsert_to_pinecone carries. find_candidate_files
            # parses it back out to tell the agent where in the file to look.
            title=_line_range_label(items),
            url="",
            config=config,
        )


def _line_range_label(items: list[tuple[dict, list[float]]]) -> str:
    """A file's indexed span, as ``"12-480"``.

    Per file rather than per chunk: ``_upsert_to_pinecone`` takes one title for
    the whole source, so a per-chunk range cannot be stored without changing
    its signature. A file-level span is enough for its job — telling the agent
    roughly where in a long file to start reading.
    """
    starts = [chunk["start_line"] for chunk, _ in items]
    ends = [chunk["end_line"] for chunk, _ in items]
    return f"{min(starts)}-{max(ends)}"


async def _record_index(
    db: AsyncSession,
    user_id: str,
    project_id: uuid.UUID | str | None,
    repo: str,
    sha: str,
    file_count: int,
) -> None:
    """Record (or update) the one row describing this project's code index.

    One row per project, updated in place rather than appended to: a project
    has exactly one code index, and a second row would make "which SHA is the
    index at" ambiguous at the moment Step 5 needs to answer it.
    """
    existing = (
        await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.user_id == user_id,
                KnowledgeSource.project_id == project_id,
                KnowledgeSource.source_type == "code",
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.source_ref = repo
        existing.source_url = f"https://github.com/{repo}"
        existing.title = repo
        existing.indexed_ref = sha
        existing.page_count = file_count
        existing.ingestion_status = "completed"
    else:
        db.add(
            KnowledgeSource(
                user_id=user_id,
                project_id=project_id,
                source_type="code",
                source_ref=repo,
                source_url=f"https://github.com/{repo}",
                title=repo,
                indexed_ref=sha,
                page_count=file_count,
                ingestion_status="completed",
            )
        )
    await db.commit()


async def get_code_index(
    db: AsyncSession, user_id: str, project_id: uuid.UUID | str | None
) -> KnowledgeSource | None:
    """This project's code-index row, or None when it has never been indexed."""
    return (
        await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.user_id == user_id,
                KnowledgeSource.project_id == project_id,
                KnowledgeSource.source_type == "code",
            )
        )
    ).scalar_one_or_none()


def _format_timings(timings: dict[str, float]) -> str:
    """Render phase timings for a log line, naming the phases that never ran.

    A phase with no entry is one that did not finish, which on a timeout is
    precisely the thing worth knowing.
    """
    return " ".join(
        f"{phase}={timings[phase]:.2f}s" if phase in timings else f"{phase}=—"
        for phase in ("embed", "connect", "query")
    )


def _candidate_from_match(match) -> tuple[str, str] | None:
    """(path, line_range) for a Pinecone match, or None if it carries no path."""
    meta = getattr(match, "metadata", None) or {}
    path = meta.get("source_id") or ""
    if not isinstance(path, str) or not path:
        return None
    lines = meta.get("title") or ""
    return path, lines if isinstance(lines, str) else ""


async def find_candidate_files(
    user_id: str,
    project_id: uuid.UUID | str | None,
    scenario_texts: list[str],
    config=None,
    top_k: int = _DEFAULT_TOP_K,
) -> list[list[dict]]:
    """Suggest which files each scenario's implementation is likely to be in.

    Returns a list aligned with ``scenario_texts``; each element is a list of
    ``{"path", "lines"}`` dicts — **deduplicated paths, never chunk text**.
    Several chunk hits in one file collapse to a single entry. That is what
    keeps the result a pointer rather than evidence, and it is also what keeps
    the injected prompt block around 200 tokens instead of 4 000.

    Never raises. Any failure — no Pinecone, no index, a timeout, a vendor
    mismatch — returns an empty list per scenario, and the run proceeds exactly
    as it does today by reading the repository tree. Discovery is an
    accelerator; a verdict must never depend on it having worked.
    """
    if not settings.pinecone_api_key or not scenario_texts:
        return [[] for _ in scenario_texts]

    namespace = code_namespace(user_id, project_id)

    # Filled as each phase finishes, and read again from the timeout handler.
    # Declared out here on purpose: a cancelled coroutine's locals are gone, so
    # timings kept inside would vanish exactly when they are wanted. "Timed
    # out" alone says nothing about WHICH phase was slow, which is the only
    # thing that tells you what to fix.
    timings: dict[str, float] = {}

    async def _retrieve() -> list[list[dict]]:
        clock = perf_counter()

        # One embeddings request for every scenario, as
        # query_knowledge_base_batch does — not one per scenario. A blank
        # scenario becomes a space so it cannot 400 the request while keeping
        # the result aligned 1:1 with the input.
        safe = [text if text.strip() else " " for text in scenario_texts]
        vectors: list[list[float]] = []
        for start in range(0, len(safe), _EMBED_BATCH_SIZE):
            vectors.extend(
                await _embed_chunks(
                    safe[start : start + _EMBED_BATCH_SIZE],
                    input_type="query",
                    config=config,
                )
            )
        timings["embed"] = perf_counter() - clock
        clock = perf_counter()

        # Timed separately because it is not free: the client resolves the
        # index host on first use, and a serverless index can be cold. Folded
        # into the query phase it would look like slow retrieval.
        pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
        index = await asyncio.to_thread(pc.Index, index_name_for(config))
        timings["connect"] = perf_counter() - clock
        clock = perf_counter()

        async def _one(vector: list[float], text: str) -> list[dict]:
            # `top_k` straight through, NOT a multiple of it.
            # `_query_matches_hybrid` already over-fetches by
            # `_QUERY_OVERFETCH` (3x) so its lexical re-rank has candidates to
            # work with, and multiplying again here made every scenario pull
            # 6x top_k chunks with their full text in metadata — the reason
            # this phase blew a 10-second budget. 3x is enough slack for
            # several chunks of one file to collapse into one candidate.
            matches = await _query_matches_hybrid(
                index, namespace, vector, text, top_k
            )
            seen: dict[str, str] = {}
            for match in matches:
                candidate = _candidate_from_match(match)
                if candidate is None:
                    continue
                path, lines = candidate
                # First hit wins: matches arrive best-first, so the strongest
                # chunk decides the line range shown for its file.
                seen.setdefault(path, lines)
                if len(seen) >= top_k:
                    break
            return [{"path": path, "lines": lines} for path, lines in seen.items()]

        raw = await asyncio.gather(
            *(_one(v, t) for v, t in zip(vectors, safe, strict=True)),
            return_exceptions=True,
        )
        timings["query"] = perf_counter() - clock
        return [item if isinstance(item, list) else [] for item in raw]

    try:
        result = await asyncio.wait_for(
            _retrieve(), timeout=_RETRIEVAL_BUDGET_SECONDS
        )
    except TimeoutError:
        logger.warning(
            "Code-index retrieval timed out after %.0fs for user=%s (%s; %d "
            "scenarios) — the agent will discover files from the repository "
            "tree instead. The phase with no time against it is the one that "
            "did not finish.",
            _RETRIEVAL_BUDGET_SECONDS,
            user_id,
            _format_timings(timings),
            len(scenario_texts),
        )
        return [[] for _ in scenario_texts]
    except Exception:
        logger.warning(
            "Code-index retrieval failed for user=%s (%s) — the agent will "
            "discover files from the repository tree instead",
            user_id,
            _format_timings(timings),
            exc_info=True,
        )
        return [[] for _ in scenario_texts]

    # Logged on SUCCESS too, not only on failure. Tuning the budget from
    # timeouts alone means learning one number per wasted run; this way a
    # working run says how much headroom there was.
    logger.info(
        "Code-index retrieval for user=%s: %d scenarios, %s, candidates=%s",
        user_id,
        len(scenario_texts),
        _format_timings(timings),
        [len(candidates) for candidates in result],
    )

    # Callers zip(strict=True) on this, so a length mismatch from an unexpected
    # embeddings count would otherwise crash the verification stream.
    if len(result) != len(scenario_texts):
        logger.warning(
            "Code-index retrieval returned %d results for %d scenarios — padding",
            len(result),
            len(scenario_texts),
        )
        result = (result + [[] for _ in scenario_texts])[: len(scenario_texts)]
    return result


def format_candidate_block(candidates: list[dict]) -> str:
    """Render candidate files as a prompt block, or "" when there are none.

    The disclaimer is not decoration. Retrieval returns the top-k most similar
    chunks whether or not any of them are relevant, so a list that looks
    authoritative but is merely nearest-neighbour output is exactly what would
    turn into a confident wrong verdict. The system prompt carries the matching
    rule; this repeats the essential half of it at the point of use.
    """
    if not candidates:
        return ""
    lines = [
        "CANDIDATE FILES (semantic search suggests these are relevant — they "
        "are suggestions, NOT evidence, and this list may be wrong or "
        "incomplete):",
    ]
    for candidate in candidates:
        span = candidate.get("lines") or ""
        suffix = f"  (lines {span})" if span else ""
        lines.append(f"  {candidate['path']}{suffix}")
    return "\n".join(lines)
