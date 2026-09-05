# Story 4.4: RAG Context Display Panel

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want to see what project knowledge was used during verification — with clickable sources and expandable snippets, both live and when I revisit a past session,
so that I can understand and trust the context behind the LLM's analysis.

## Acceptance Criteria

1. **Given** verification is complete and RAG retrieval results exist for the session
   **When** the verification results panel renders
   **Then** the existing `RAGContextPanel` component displays the retrieved knowledge sources (Confluence page titles, Jira story IDs, project artifacts) with **expandable snippets** — each snippet is truncated by default and expands to full text on click *(FR36)*

2. **And** each knowledge source is clickable/identifiable by type:
   - **Confluence** items render as a link that opens the source page in a new tab (`target="_blank"` + `rel="noopener noreferrer"`)
   - **Jira** items display the ticket ID and the ticket **title/summary** (as a link to the Jira browse URL when available)

3. **And** if no RAG context was retrieved for a completed verification, the panel shows the message **"No additional project context available"** — rendered as an informational (non-error) empty state, **not** hidden and **not** an error

4. **And** the RAG context is **persisted and displayed on session revisit** — reloading a past session via `GET /api/v1/sessions/{id}/verification-results` returns the stored `rag_context`, and the `RAGContextPanel` renders it identically to the live verification flow *(FR36)*

## Context & Critical Background

> 🔴 **READ THIS FIRST — this story is an ENHANCEMENT, not a greenfield build.**

Story 4.3 (RAG-Enriched Verification Integration, **done**) already:

- Created `frontend/src/components/pipeline/RAGContextPanel.tsx` (violet-styled list, renders `source`/`source_id`/`snippet`)
- Integrated it into `VerificationResultRow.tsx` (rendered inside the expanded verdict body, guarded by `rag_context && length > 0`)
- Added the `RagContextItem` type (frontend) and Pydantic model (backend) with fields `source`, `source_id`, `snippet`
- Added the `rag_context` JSONB **column** to the `verification_results` table (migration `f5a6b7c8d9e0`)
- Wired `rag_context` into the **live** SSE verdict event and DB persistence

**What 4.4 must ADD on top of that foundation:**

| AC | Gap in current code | Required change |
|----|---------------------|-----------------|
| AC1 | `RAGContextPanel` uses `line-clamp-2` (no expand) | Per-item expand/collapse toggle |
| AC2 | `RagContextItem` has no `title`/`url`; Pinecone metadata stores only `{text, source_id, source}` | Add `title`+`url` to ingest metadata + `RagContextItem` + panel link rendering |
| AC3 | `RAGContextPanel` does `return null` on empty; `VerificationResultRow` only renders it when `length > 0` | Panel renders empty-state message; row always renders panel after a completed verification |
| AC4 | `StoredVerificationResult` schema **omits** `rag_context`; session page mapping drops it | Add field to schema + frontend type; map it in the revisit `useEffect` |

## Tasks / Subtasks

### Backend — expose `rag_context` on session revisit (AC4)

- [x] **Task 1: Add `rag_context` to `StoredVerificationResult` schema** (AC: 4)
  - [x] In `backend/app/schemas/session.py`, add `rag_context: list[dict] | None = None` to `StoredVerificationResult` (keep `model_config = ConfigDict(from_attributes=True)` — the field auto-populates from the ORM `rag_context` column via `model_validate`)
  - [x] No route change needed — `sessions.py:get_session_verification_results` already does `StoredVerificationResult.model_validate(r)`; the new field flows through automatically

### Backend — enrich source metadata for clickable titles/links (AC2)

- [x] **Task 2: Store `title` + `url` in Pinecone chunk metadata** (AC: 2)
  - [x] In `backend/app/services/knowledge_service.py`, extend `_upsert_to_pinecone(...)` signature with `title: str = ""` and `url: str = ""`
  - [x] Add `"title": title` and `"url": url` to the per-vector `metadata` dict (alongside existing `text`/`source_id`/`source`)
  - [x] In `ingest_confluence`, pass `title=page.title, url=page.url` to `_upsert_to_pinecone`
  - [x] In `ingest_jira`, pass `title=ticket.summary, url=f"{settings.jira_base_url.rstrip('/')}/browse/{ticket.ticket_id}"` to `_upsert_to_pinecone`

- [x] **Task 3: Return `title` + `url` from `query_knowledge_base`** (AC: 2)
  - [x] In `knowledge_service.query_knowledge_base._retrieve()`, add to each returned dict:
    - `"title": (m.metadata or {}).get("title", "")`
    - `"url": (m.metadata or {}).get("url", "")`
  - [x] **Graceful fallback for legacy data** (chunks ingested before this story lack `title`/`url` metadata): when `url` is empty **and** `source == "jira"` and `settings.jira_base_url` is set, derive `url = f"{settings.jira_base_url.rstrip('/')}/browse/{source_id}"`. When `title` is empty, leave it empty (frontend falls back to `source_id`).

- [x] **Task 4: Add `title` + `url` to `RagContextItem` Pydantic model** (AC: 2)
  - [x] In `backend/app/schemas/verification.py`, add to `RagContextItem`:
    - `title: str = Field(default="", description="Human-readable source title (Confluence page title / Jira summary)")`
    - `url: str = Field(default="", description="Deep link to the source; empty when not linkable")`
  - [x] Keep both **optional with defaults** so existing persisted `rag_context` JSON (no title/url) still validates and legacy tests pass
  - [x] In `verification_service.py::run_verification`, when building `rag_context_payload`, pass through `title=c.get("title", "")` and `url=c.get("url", "")` from the query result dicts

### Frontend — types (AC2, AC4)

- [x] **Task 5: Extend `RagContextItem` type** (AC: 2)
  - [x] In `frontend/src/lib/types/verification.ts`, add to `RagContextItem`:
    - `title?: string;`
    - `url?: string;`

- [x] **Task 6: Add `rag_context` to `StoredVerificationResult` type** (AC: 4)
  - [x] In `frontend/src/lib/types/session.ts`, add `rag_context?: RagContextItem[] | null;` to `StoredVerificationResult`
  - [x] Import `RagContextItem` from `@/lib/types/verification` (or re-declare — prefer import to keep a single source of truth)

### Frontend — enhance the panel (AC1, AC2, AC3)

- [x] **Task 7: Expandable snippets + clickable sources + empty state in `RAGContextPanel`** (AC: 1, 2, 3)
  - [x] Change the empty guard: instead of `if (!items || items.length === 0) return null;`, render the empty-state card with **"No additional project context available"** (informational styling — muted/slate, `role` not `alert`)
  - [x] Convert to a client component with per-item expand state (`useState<Set<number>>` or an index-keyed boolean map). Default: snippet truncated (`line-clamp-2`); on click of the item (or a "Show more/less" affordance) toggle to full snippet
  - [x] Render the source identifier by type:
    - `item.source === "confluence"` and `item.url` present → render title (fallback `source_id`) as `<a href={item.url} target="_blank" rel="noopener noreferrer">` with an external-link icon
    - `item.source === "jira"` → show `source_id` (ticket ID) + `title`; wrap in `<a>` to `item.url` (new tab) when `url` present, otherwise plain text
    - No `url` → render as non-clickable text (never render a dead/`#` link)
  - [x] Preserve existing violet visual language and the `BookOpen` header; keep accessibility (`aria-expanded` on the toggle, keyboard-activatable)

- [x] **Task 8: Always render the panel after a completed verification** (AC: 3)
  - [x] In `VerificationResultRow.tsx`, replace the guard `{verdict.rag_context && verdict.rag_context.length > 0 && (<RAGContextPanel .../>)}` so the panel renders whenever the verdict is shown (pass `items={verdict.rag_context ?? []}`), letting the panel own the empty state
  - [x] Do **not** render the panel while a scenario is still streaming/pending if that would flash the empty state prematurely — it renders in the expanded body which only appears for completed verdicts, so this is already safe

### Frontend — session revisit mapping (AC4)

- [x] **Task 9: Map `rag_context` when restoring saved results** (AC: 4)
  - [x] In `frontend/src/app/session/[sessionId]/page.tsx`, inside the `existingVerificationResults.map(...)` that builds `VerificationVerdict[]`, add `rag_context: (r.rag_context ?? null) as RagContextItem[] | null,`
  - [x] Import `RagContextItem` alongside the existing `CodeReference, VerificationVerdict` type import

### Tests

- [x] **Task 10: Backend tests** (AC: 2, 4)
  - [x] In `backend/tests/test_rag_verification.py` (or a new `test_rag_context_display.py`): assert `query_knowledge_base` returns `title` + `url` keys when metadata includes them
  - [x] Assert the Jira legacy fallback: metadata missing `url`, `source="jira"`, `settings.jira_base_url` set → derived browse URL returned
  - [x] In `backend/tests/test_sessions.py`: assert `GET /sessions/{id}/verification-results` returns `rag_context` in the payload when the stored row has one, and `null`/absent when it does not
  - [x] Confirm no regression: existing 4.3 tests (persisted `rag_context` without title/url) still pass because the new schema fields default to `""`

- [x] **Task 11: Frontend tests** (AC: 1, 2, 3)
  - [x] Add `frontend/src/components/pipeline/__tests__/RAGContextPanel.test.tsx` following the existing pattern in `__tests__/VerificationResultsPanel.test.tsx` (Vitest + `@testing-library/react`)
  - [x] Cases: renders items with source badges; Confluence item renders an `<a target="_blank">` with the title; Jira item shows ticket ID + title; snippet expands on click; empty/`[]` renders "No additional project context available"; item with no `url` renders as text (no anchor)

## Dev Notes

### Design decision: where do `title` and `url` come from? (AC2)

The retrieved chunk currently carries only `{source, source_id, snippet}` because `_upsert_to_pinecone` stored only `{text, source_id, source}` in Pinecone metadata. Two viable sources for title/url were considered:

1. **DB join against `knowledge_sources`** — rejected. `knowledge_sources` has `title` + `source_url` but **no explicit `source_id`/`page_id` column** to join on. For Jira the ticket ID is only embedded inside `source_url`; for Confluence the page ID is not stored at all. Fuzzy matching would be unreliable.
2. **Enrich Pinecone chunk metadata at ingestion** ✅ chosen — the ingestion code already has `page.title`/`page.url` and `ticket.summary`/`ticket.ticket_id` in hand. Storing them in metadata makes retrieval a pure metadata read, no extra DB round-trip, and keeps the source of truth with the chunk.

**Consequence:** chunks ingested by Stories 4.1/4.2 **before** this change have no `title`/`url` in their metadata. This story handles that gracefully (Task 3 fallback + frontend `source_id` fallback), and any re-ingestion will populate the richer metadata. This is acceptable for the current greenfield state (Epic 4 was just built). See open question Q1.

### AC4 is mostly plumbing — the column already exists

`verification_results.rag_context` (JSONB) was added in 4.3 (migration `f5a6b7c8d9e0`) and is already written on every live run. The **only** reason revisit doesn't show it is that `StoredVerificationResult` (both the Pydantic schema and the TS type) never declared the field, and the session-page mapping never copied it. No migration, no new endpoint — just schema field + TS field + one line in the `.map()`.

Because `sessions.py:get_session_verification_results` uses `StoredVerificationResult.model_validate(r)` with `from_attributes=True`, adding the field to the schema is sufficient on the backend — the route needs no edit.

### Existing code patterns to reuse (DO NOT reinvent)

- **Pinecone metadata upsert** — [Source: backend/app/services/knowledge_service.py:81-94] — extend the existing `metadata` dict; do not create a new vector shape
- **`query_knowledge_base` retrieval** — [Source: backend/app/services/knowledge_service.py:113-135] — add keys to the existing returned dict; keep the try/except + `asyncio.wait_for(timeout=10.0)` graceful-degradation wrapper intact (NFR-P6, NFR-R3)
- **`RagContextItem` payload build** — [Source: backend/app/services/verification_service.py] — the `rag_context_payload` list comprehension already maps query dicts → `RagContextItem`; add `title`/`url` there
- **External link rendering** — [Source: frontend/src/components/pipeline/VerificationResultRow.tsx:72-87] — the GitHub-links block is the exact `<a target="_blank" rel="noopener noreferrer">` + `ExternalLink` icon pattern to mirror for Confluence/Jira links
- **Panel component** — [Source: frontend/src/components/pipeline/RAGContextPanel.tsx] — enhance in place; keep violet palette, `BookOpen` header, `line-clamp-2` as the collapsed state
- **Revisit mapping** — [Source: frontend/src/app/session/[sessionId]/page.tsx:199-215] — add `rag_context` to the object literal inside the existing `map`; the effect + refs already handle "populate once per visit"

### Architecture compliance

- `RAGContextPanel` + `VerificationResultRow` are the designated display surface for RAG context [Source: _bmad-output/planning-artifacts/architecture.md:357, 450]
- No raw `fetch`/`axios` in components — revisit data arrives via the existing `useSessionVerificationResults` hook [Source: frontend/src/lib/hooks/useSession.ts:58-71]
- Route handlers stay logic-free — Task 1 is a schema-only change; `sessions.py` is untouched
- Namespace isolation unchanged — no new Pinecone query paths; reuse `{user_id}:knowledge`
- TypeScript strict: no `any`; use `RagContextItem[] | null` and `unknown` casts only where the codebase already does (e.g. `code_reference`)

### Project Structure Notes

**Files to modify (backend):**
- `backend/app/schemas/session.py` — add `rag_context` to `StoredVerificationResult`
- `backend/app/schemas/verification.py` — add `title`, `url` to `RagContextItem`
- `backend/app/services/knowledge_service.py` — `_upsert_to_pinecone` metadata + both ingest callers + `query_knowledge_base` return + Jira URL fallback
- `backend/app/services/verification_service.py` — pass `title`/`url` into `rag_context_payload`

**Files to modify (frontend):**
- `frontend/src/lib/types/verification.ts` — `RagContextItem` gets `title?`, `url?`
- `frontend/src/lib/types/session.ts` — `StoredVerificationResult` gets `rag_context?`
- `frontend/src/components/pipeline/RAGContextPanel.tsx` — expand/collapse, links, empty state
- `frontend/src/components/pipeline/VerificationResultRow.tsx` — always render panel
- `frontend/src/app/session/[sessionId]/page.tsx` — map `rag_context` on revisit

**Files to create:**
- `frontend/src/components/pipeline/__tests__/RAGContextPanel.test.tsx`
- (optional) `backend/tests/test_rag_context_display.py` — or extend `test_rag_verification.py` / `test_sessions.py`

**Do NOT modify:**
- `backend/app/api/v1/sessions.py` — schema change flows through `model_validate` automatically
- `backend/app/api/v1/verification.py` — RAG is internal to the service
- `backend/app/models/verification_result.py` — `rag_context` column already exists
- Any Alembic migration — no schema/DB change in this story
- Any auth/session-isolation code

### Testing Standards

- **Backend:** `pytest` + `pytest-asyncio` with `AsyncMock`; mock `_embed_chunks` → `[[0.1]*1536]` and `pinecone.Pinecone`/`index.query`. Match the fixtures already in `test_rag_verification.py`. Run `pytest` from `backend/` and ensure zero regressions across the full suite; run `ruff` clean.
- **Frontend:** Vitest + `@testing-library/react` (see `__tests__/VerificationResultsPanel.test.tsx` for setup/render/query conventions). Run `npm run lint` + `tsc --noEmit` (or the project's typecheck script) clean.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 4, Story 4.4 acceptance criteria (FR36)
- [Source: _bmad-output/implementation-artifacts/4-3-rag-enriched-verification-integration.md] — foundation: RAGContextPanel, RagContextItem, rag_context column + live wiring
- [Source: _bmad-output/implementation-artifacts/3-5-session-history-dashboard.md] — session revisit path (`useSessionVerificationResults`, `StoredVerificationResult`, results→verdict mapping)
- [Source: backend/app/services/knowledge_service.py:67-151] — upsert + query patterns to extend
- [Source: backend/app/schemas/verification.py] — `RagContextItem`, `VerificationVerdict`
- [Source: backend/app/schemas/session.py:34-48] — `StoredVerificationResult`
- [Source: backend/app/api/v1/sessions.py:138-168] — revisit endpoint (uses `model_validate`, no edit needed)
- [Source: frontend/src/components/pipeline/RAGContextPanel.tsx] — component to enhance
- [Source: frontend/src/components/pipeline/VerificationResultRow.tsx:72-102] — external-link pattern + panel render guard
- [Source: frontend/src/app/session/[sessionId]/page.tsx:199-215] — revisit results mapping
- [Source: backend/app/core/config.py:43] — `jira_base_url` (for Jira URL derivation)
- [Source: _bmad-output/planning-artifacts/architecture.md:357, 450] — RAGContextPanel is the designated RAG display surface

### Open Questions (for reviewer — do not block implementation)

- **Q1 — Legacy chunk re-ingestion:** Chunks ingested in 4.1/4.2 lack `title`/`url` metadata. Task 3's fallback handles Jira URLs and title→`source_id` degradation, but Confluence links will be absent for legacy chunks until re-ingestion. Acceptable for MVP? Or should the story include a one-off re-ingestion note in the Epic 4 retro?
- **Q2 — Empty-state noise:** AC3 requires "No additional project context available" whenever a completed verification has no RAG context. Since RAG is queried once per run, this message appears in **every** expanded verdict for users who haven't set up a knowledge base. Confirm this is desired vs. showing it once at the panel/summary level. Following the AC literally for now (per-verdict).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

Two pre-existing failures surfaced during validation (neither caused by this story):

1. **`test_sessions.py` MagicMock trap (fixed proactively):** Adding `rag_context` to `StoredVerificationResult` with `from_attributes=True` means `model_validate(vr)` reads `vr.rag_context`. The shared `_make_verification_result` helper builds a `MagicMock`, so this would have returned an auto-Mock and failed Pydantic validation — breaking the 3 existing verification-results tests. Fixed by setting `vr.rag_context = None` explicitly in the helper (same class of fix Story 3.5 applied for `bdd_status`).

2. **Ingestion namespace-isolation tests (fixed):** `test_knowledge.py` / `test_jira_knowledge.py` stub `_upsert_to_pinecone` with a `_capture_upsert` whose signature didn't accept the new `title`/`url` kwargs → `TypeError`. Fixed by adding `**kwargs` to the 4 stub signatures (preserves their namespace-capture intent).

3. **`VerificationResultsPanel.test.tsx` (pre-existing, partially fixed):** Its `useSessionContext` mock omitted `bddContent`/`isVerifying`, which the component reads at `VerificationResultsPanel.tsx:16` — the whole file threw at `bddContent.match` before reaching any assertion (verified pre-existing via `git stash`). Added `isVerifying: false, bddContent: ""` to the mock → 19/20 now pass. The 1 remaining failure ("renders mobile read-only overlay element (AC 6)") asserts text that no longer exists in the component (a removed Story-2.4 mobile overlay) — orphaned assertion, out of scope for Story 4.4.

### Completion Notes List

- **AC2 data source** — Chose to enrich Pinecone chunk metadata with `title`+`url` at ingestion (rejected a `knowledge_sources` DB join, which has no reliable `source_id`/`page_id` join key — see Dev Notes). Added a `_chunk_from_match()` helper in `knowledge_service.py` that reads `title`/`url` from metadata with graceful fallbacks: legacy Jira chunks derive their browse URL from `settings.jira_base_url + source_id`; missing titles fall back to `source_id` in the UI. **Legacy chunks ingested in 4.1/4.2 lack Confluence URLs until re-ingested (open question Q1).**
- **AC4** — Was almost pure plumbing: the `rag_context` JSONB column already existed (4.3). Added the field to `StoredVerificationResult` (Pydantic + TS) and one line in the session-page `.map()`. No migration, no route change (the endpoint already uses `model_validate`).
- **AC1/AC3** — `RAGContextPanel` rewritten: per-item Show more/less snippet toggle; empty/`[]` now renders "No additional project context available" (informational, not null); `VerificationResultRow` always renders the panel so the empty state shows on completed verdicts.
- **AC2 links** — Confluence renders title as an `<a target="_blank" rel="noopener noreferrer">`; Jira renders `{ticket_id} — {title}` linked to the browse URL; no `url` → plain text (never a dead link). Mirrors the existing GitHub-links anchor pattern in `VerificationResultRow`.
- **Backward compatibility** — All new backend fields (`RagContextItem.title/url`) default to `""` so `rag_context` persisted by Story 4.3 (no title/url) still validates; all new TS fields are optional.
- **Validation** — Backend: **198 passed**, production code ruff-clean. Frontend: new `RAGContextPanel.test.tsx` **8/8**; my changed files are eslint- and tsc-clean (only the pre-existing `useKnowledge.ts` type error remains, untouched).

### File List

**Backend — modified:**
- `backend/app/schemas/session.py` — added `rag_context: list[dict] | None` to `StoredVerificationResult`
- `backend/app/schemas/verification.py` — added `title`/`url` (default "") to `RagContextItem`
- `backend/app/services/knowledge_service.py` — `_upsert_to_pinecone` stores `title`/`url` in metadata; both ingest callers pass them; new `_chunk_from_match()` helper with Jira URL fallback; `query_knowledge_base` returns enriched dicts
- `backend/app/services/verification_service.py` — pass `title`/`url` through when building `RagContextItem` payload

**Backend — tests:**
- `backend/tests/test_rag_verification.py` — added `TestQueryKnowledgeBaseTitleUrl` (3 tests: metadata title/url, Jira legacy fallback, Confluence empty-url)
- `backend/tests/test_sessions.py` — set `vr.rag_context = None` in helper; added 2 tests (rag_context present / null on revisit)
- `backend/tests/test_knowledge.py` — `_capture_upsert` stubs accept `**kwargs`
- `backend/tests/test_jira_knowledge.py` — `_capture_upsert` stubs accept `**kwargs`

**Frontend — modified:**
- `frontend/src/lib/types/verification.ts` — `RagContextItem` gains optional `title?`/`url?`
- `frontend/src/lib/types/session.ts` — `StoredVerificationResult` gains optional `rag_context?`; imports `RagContextItem`
- `frontend/src/components/pipeline/RAGContextPanel.tsx` — expandable snippets, clickable Confluence/Jira sources, empty-state message
- `frontend/src/components/pipeline/VerificationResultRow.tsx` — always render the panel for completed verdicts
- `frontend/src/app/session/[sessionId]/page.tsx` — map `rag_context` when restoring saved results

**Frontend — tests:**
- `frontend/src/components/pipeline/__tests__/RAGContextPanel.test.tsx` — new (8 tests: empty state, Confluence link, Jira ID+title, no-url plain text, title fallback, expand toggle, multi-item)
- `frontend/src/components/pipeline/__tests__/VerificationResultsPanel.test.tsx` — fixed stale `useSessionContext` mock (added `bddContent`/`isVerifying`); skipped orphaned AC6 test (review M2)

**Repo config — modified (review M1):**
- `.gitignore` — added ignore rules for `.env copy` duplicate secret files

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (adversarial code-review workflow) · **Date:** 2026-07-04 · **Outcome:** Approve (all findings fixed)

Git File List matched actual changes exactly (no false claims). All 4 ACs verified as genuinely implemented; all tasks `[x]` confirmed against code. Stress-tested the riskiest path — Pinecone rejects `null` metadata values — and confirmed `ConfluencePage.title/url` and `JiraTicketContent.summary` are non-nullable `str`, so the metadata enrichment is safe.

### Action Items — all resolved

- [x] **[M1][Med]** `.env copy` and `backend/.env copy` were staged for commit (secrets hygiene) → unstaged and added ignore rules to `.gitignore` (`git check-ignore` now matches both).
- [x] **[M2][Med]** Mock fix un-masked orphaned `VerificationResultsPanel` AC6 test (asserts removed mobile-overlay text) → converted to `it.skip` with an explanatory comment rather than a false-green delete. `[VerificationResultsPanel.test.tsx:213]`
- [x] **[M3][Med]** Live `run_verification` path never asserted title/url persistence → added `test_title_and_url_propagated_to_event_and_db` (checks both SSE event and DB row). `[test_rag_verification.py]`
- [x] **[L1][Low]** "Show more/less" rendered unconditionally on short snippets → gated on `isTruncatable` (snippet length > 100). `[RAGContextPanel.tsx]`
- [x] **[L2][Low]** `_chunk_from_match(match)` param annotation → left untyped to match the file's existing untyped Pinecone-match handling (`for m in results.matches`); over-annotating with `object` would be semantically wrong.

**Post-fix validation:** backend **199 passed** (+1 new test), app code ruff-clean; frontend panel suites **19 passed / 1 skipped**, changed files eslint- and tsc-clean.

## Change Log

- 2026-07-04: Implemented Story 4.4 — RAG Context Display Panel. Enhanced `RAGContextPanel` (expandable snippets, clickable Confluence/Jira sources, empty state); enriched Pinecone chunk metadata + retrieval with `title`/`url` (graceful legacy fallbacks); exposed persisted `rag_context` on session revisit (`StoredVerificationResult` + session-page mapping). Backend 198 tests pass; new frontend panel suite 8/8. Fixed 2 pre-existing test-mock issues surfaced by the schema/signature changes.
- 2026-07-04: Code review (adversarial) — Approve. Resolved 3 Medium + 2 Low findings: unstaged/ignored `.env copy` secrets files; skipped orphaned AC6 test; added live-path title/url persistence test; gated snippet toggle on truncation. Backend 199 pass; frontend panel suites green (1 documented skip). Added `.gitignore` to File List.
