# Maintenance Record — Verification & RAG Hardening

**Date:** 2026-08-22
**Kind:** Cross-cutting maintenance pass (bugfixes + accuracy hardening), performed outside the story workflow
**Status:** ✅ Implemented, all backend tests passing (616) at time of writing
**Amends:** Stories 1.4, 2.2, 2.3, 2.5, 3.3, 3.5, 4.3, 4.6, 5.5 (each carries a pointer to this record)

This record exists so future agents reading the story artifacts do not implement
against behaviour those stories describe but the codebase no longer has. Each
change below names the files that carry it; the code and its tests are the
source of truth.

---

## 1. Session contract fixes

**`bdd_status` accepts `"edited"`** — `POST /bdd/save` (Story 6.4) writes
`bdd_files.source = "edited"`, but `SessionResponse.bdd_status` only allowed
`none | generated | uploaded`, so any session whose newest BDD row was an edit
500'd the entire `GET /sessions` list. The literal now includes `"edited"`,
mirrored in the frontend types and rendered as its own "Edited" badge.
Files: `backend/app/schemas/session.py`, `frontend/src/lib/types/session.ts`,
`frontend/src/app/sessions/page.tsx`.

**`jira_ticket_url` persisted** — the text the user actually submitted at
ingestion (full Jira URL or bare key) is now stored on `sessions` and returned
by both session GETs, so revisiting a session restores the ticket field instead
of clearing it. Nullable, no backfill; upload-created sessions have none.
Migration: `f6b7c8d9e0a1_add_jira_ticket_url_to_sessions` (applied).
Files: `backend/app/models/session.py`, `backend/app/api/v1/ingestion.py`,
`frontend/src/context/SessionContext.tsx`,
`frontend/src/app/session/[sessionId]/page.tsx`.

**Sessions list is paginated** — `GET /api/v1/sessions` takes `limit`
(default 20, max 100) / `offset` and returns an envelope
`{items, total, limit, offset}` instead of a bare array. `total` counts all the
user's sessions so the client can size the pager and self-correct an
out-of-range page. Ordering gained an `id` tiebreaker so same-instant sessions
cannot repeat/vanish across pages. The dashboard has Previous/Next controls,
cross-page row numbering, and `keepPreviousData` so paging doesn't flash a
skeleton.
Files: `backend/app/api/v1/sessions.py`, `backend/app/schemas/session.py`,
`frontend/src/lib/hooks/useSession.ts`, `frontend/src/app/sessions/page.tsx`.

## 2. Agentic verification accuracy (Story 2.5 semantics tightened)

The live path treated all three source modes as "explore `owner/repo` at
default-branch HEAD". Now each mode verifies the code the user actually chose,
via a `_VerificationScope` resolved per run:

- **Exact files**: the listed files are pre-fetched (each at its own URL's ref)
  and injected into the prompt as the code under verification; tools are pinned
  to the first URL's resolved ref. URL lists spanning two repositories are
  refused instead of silently using the first.
- **Pull request**: the PR's changed-file diffs are injected as evidence and
  tools are pinned to the **PR head commit SHA** — previously the PR number was
  discarded entirely and verdicts were about the default branch.
- **Full repo**: unchanged (agent explores via tools).

Two grounding rules were added on top:

- **Evidence guard**: a `pass` verdict requires at least one successful tool
  read or injected evidence; an evidence-free pass is forced to `fail` with an
  explanatory suggestion. (The old guard only fired when a tool call *errored*,
  so a zero-tool-call hallucinated pass sailed through.)
- **Identity pinning**: `scenario_id`/`scenario_title` are overwritten with the
  known scenario values, never trusted from the model's echo.

Files: `backend/app/services/agentic_verification_service.py`,
`backend/app/services/github_service.py` (new `get_pr_head_sha`; PR evidence
links use the head SHA, not `blob/HEAD` which 404s for unmerged branches).

## 3. GitHub fetching limitations removed

- **Slash-containing branch names** (`feature/my-branch`) in blob URLs now
  resolve: on a 404 the real branch is recovered via the `matching-refs` API
  (longest-prefix match) and the fetch retried. Slashed *tag* names remain
  unsupported. `fetch_exact_files_resolved` also returns the resolved ref for
  tool pinning.
- **PR file listings paginate** to GitHub's own 3 000-file limit (30 × 100),
  with a `[WARNING]` entry beyond it — previously silently truncated at 100.

## 4. Legacy two-step verification path removed

The UI has been agentic-only since Story 2.5; the dead pre-fetch path was
removed wholesale on 2026-08-22:

- Endpoints `POST /verification/fetch` and `POST /verification/run`
- `verification_service.run_verification` (+ its prompt builder and link
  resolver) — the module now holds only the shared substrate: Gherkin parsing,
  file-relevance selection, RAG formatting, verdict wire shape, row builder
- `github_service.fetch_github_code` dispatcher and `fetch_full_repo`
  (full-repo mode needs no prefetch)
- Schemas `VerificationFetchRequest/Response`, `VerificationRunRequest`
- Frontend: the unused `useVerification()` fetch mutation, `FetchedFile` /
  `VerificationFetchResponse` / `GitHubSourceInput` types, and the
  never-populated `fetchedFiles` context state

**Stories 2.2 and 2.3 describe this removed flow** — read them as history, not
as the current API surface.

## 5. Embedding & RAG hardening (Stories 4.x / 5.x semantics tightened)

- **No random-embedding fallback.** Previously, any non-OpenAI
  `LLM_PROVIDER` silently indexed *random vectors* — garbage that retrieval
  then served as "PROJECT CONTEXT". Now `EmbeddingsUnavailableError`: read
  paths degrade to no-context, ticket indexing skips with a log (session still
  works), KB ingestion aborts with one clear `EMBEDDINGS_UNAVAILABLE` error.
  `_embed_chunks` is canonical in `vector_service` (re-exported from
  `knowledge_service` for compatibility).
- **Full-text grounding.** LLM prompts (KB chat, verification PROJECT CONTEXT)
  ground in the full retrieved chunk; the 300-char `snippet` is only for UI
  display and verdict persistence.
- **Stale-chunk cleanup.** `_upsert_to_pinecone` clears a source's existing
  vectors before re-upserting, so re-ingesting a source that shrank cannot
  leave deleted tail content retrievable.
- **Document re-upload dedupe (amends Story 4.6).** Document source ids are
  filename-keyed (`doc-{sha256(filename)[:16]}`), so re-uploading replaces the
  previous version — vectors via the pre-upsert cleanup, and the
  `knowledge_sources` row is updated in place instead of duplicated.
  Pre-existing uuid-keyed documents need one manual delete before dedupe
  applies to them.
- **Hybrid-lite retrieval (amends Stories 4.3 / 4.8 / 5.5).**
  `query_knowledge_base(_batch)` now: over-fetches the vector query
  (`top_k × 3`), re-ranks by verbatim identifier hits (ticket keys,
  snake_case/camelCase/`call()` tokens), and — when the query names ticket
  keys — runs one extra metadata-filtered query (`source_id ∈ keys`) whose
  hits merge in front, bypassing the relevance floor. Plain prose queries
  still cost exactly one Pinecone call.

## 6. Test surface

Suites reshaped alongside: `test_verification.py` (github_service only, no
endpoint tests), `test_verification_service.py` (parse only),
`test_rag_verification.py` (knowledge retrieval only), plus new
`test_embedding_policy.py` and `test_hybrid_retrieval.py`, and mode-scope /
grounding coverage in `test_agentic_verification_service.py`.
