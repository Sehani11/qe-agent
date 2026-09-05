# Story 4.5: Knowledge Base Ingestion UI

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-projects-and-credential-scoping.md)): Ingestion and the sources list are scoped to the active project. The sources query carries the project in its React Query key, so switching refetches rather than serving the previous project's rows.


Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-ui-redesign-feature-file.md)): The "rose alert style" this story reuses is now the `fail` signal token with a `.gutter-rule`, and the amber degraded note is the `pending` token; `ErrorNote`/`ResultNote` keep their names, copy and roles. The sources list renders `SkeletonRows` while loading instead of a spinner, and ingest/delete outcomes now **also** raise a toast. The inline notes were kept — the toast is additive.

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-ingest-by-url.md)): Both forms gained a `role="tablist"` method selector matching `GitHubSourceSelector` — Confluence picks "Whole space" or "Page URLs", Jira picks "Whole project" or "Ticket URLs". The whole-space/project method remains the default. The standalone "or Page ID" field is gone; bare ids are now accepted in the Page URLs textarea, so the empty-space-key validation message no longer mentions a page ID.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want a UI to connect and ingest Confluence pages and Jira workspace tickets into my knowledge base, and to see what I've already ingested,
so that I can populate the RAG knowledge base from the app without calling the API manually.

## Acceptance Criteria

1. **Given** an authenticated user opens the Knowledge Base ingestion UI (a dedicated `/knowledge` route, reachable via a link from the dashboard)
   **When** the page renders
   **Then** it shows two ingestion forms — **Confluence** (space key *or* page ID) and **Jira** (project key + optional sprint/label) — plus a list of previously ingested sources

2. **And** submitting a form invokes the existing `useIngestConfluence` / `useIngestJira` hook, streaming live progress (current message, e.g. "Processing: {title}") while `isIngesting` is true, and a success state showing the final `ingested_count` on the `complete` event

3. **And** `error` SSE events (or transport failures) surface an actionable inline message — never a silent failure (NFR-R1)

4. **And** the "Ingested Sources" list is populated from a new `GET /api/v1/knowledge/sources` endpoint returning the current user's `KnowledgeSource` rows (source type, title, URL, page count, ingestion status, date), scoped to `current_user` — no cross-user leakage (NFR-S8); the list refreshes after a successful ingestion

5. **And** client-side validation blocks obviously-invalid input before submit: Confluence requires space key **or** page ID; Jira `project_key` must match the backend rule (`^[A-Z][A-Z0-9_]{0,9}$`, uppercased)

6. **And** when the backend has no Pinecone/credentials configured, ingestion still returns gracefully (0 ingested) and the UI shows a clear informational note rather than appearing broken

## Context & Critical Background

> 🔴 **This is a UI-surfacing story, not new ingestion logic.** Stories 4.1/4.2 built the ingestion **endpoints** and the **frontend hooks** API-only — no screen was ever specced (every 4.1/4.2 AC is `"POST request is made to..."`). This story renders that existing capability.

**Already built (reuse, do NOT reinvent):**

| Piece | Location | Status |
|---|---|---|
| Confluence/Jira ingest hooks (SSE + Supabase auth + callbacks) | `frontend/src/lib/hooks/useKnowledge.ts` | ✅ done, unused |
| Request/SSE/source TS types | `frontend/src/lib/types/knowledge.ts` | ✅ done |
| Ingest endpoints (SSE streaming) | `backend/app/api/v1/knowledge.py` | ✅ done |
| `KnowledgeSource` model + `KnowledgeSourceResponse` schema | `backend/app/models/knowledge_source.py`, `backend/app/schemas/knowledge.py` | ✅ done |

**Gaps this story fills:**
- No UI renders the ingest hooks → build a `/knowledge` page + `KnowledgeBasePanel`.
- No **`GET /api/v1/knowledge/sources`** endpoint → add it (schema already exists).
- No `useKnowledgeSources` query hook → add it.

## Tasks / Subtasks

### Backend — list endpoint (AC4)

- [x] **Task 1: Add `GET /api/v1/knowledge/sources`** (AC: 4)
  - [x] In `backend/app/api/v1/knowledge.py`, add a `list_sources` route: `@router.get("/sources", response_model=list[KnowledgeSourceResponse])`
  - [x] Depend on `get_current_user` + `get_db`; query `KnowledgeSource` where `user_id == current_user` ordered by `created_at DESC`
  - [x] Return `[KnowledgeSourceResponse.model_validate(r) for r in rows]` (schema has `from_attributes=True`)
  - [x] Keep the route handler logic-free beyond the scoped query (architecture rule)

- [x] **Task 2: Backend tests** (AC: 4)
  - [x] New `backend/tests/test_knowledge_sources.py` (or extend `test_knowledge.py`): owner gets only their rows; empty list when none; user isolation (user A never sees user B's sources). Mock the DB session like `test_sessions.py` does.

### Frontend — data hook + types (AC4)

- [x] **Task 3: Add `useKnowledgeSources` TanStack Query hook** (AC: 4)
  - [x] In `frontend/src/lib/hooks/useKnowledge.ts` (or a new `useKnowledgeSources.ts`), add `useKnowledgeSources()` using `apiClient.get<KnowledgeSource[]>("/knowledge/sources")` (the axios client already attaches the JWT — see `lib/api/client.ts`)
  - [x] queryKey `["knowledge", "sources"]`; expose `refetch` so the panel can refresh after ingestion
  - [x] Note: the ingest hooks use raw `fetch` (required for SSE streaming); the list hook should use the existing `apiClient` for consistency with other non-streaming calls

### Frontend — the panel (AC1, AC2, AC3, AC5, AC6)

- [x] **Task 4: Build `KnowledgeBasePanel` component** (AC: 1, 2, 3, 5)
  - [x] Create `frontend/src/components/knowledge/KnowledgeBasePanel.tsx` (new folder)
  - [x] **Confluence form:** inputs for `space_key` and `page_id`; wire `useIngestConfluence({ onProgress, onComplete, onError })`; disable submit while `isIngesting`
  - [x] **Jira form:** input for `project_key` (+ optional `sprint`, `label`); wire `useIngestJira(...)`; disable submit while `isIngesting`
  - [x] **Progress:** render the hook's `progressMessage` + a spinner while `isIngesting`; on complete, show "Ingested N item(s)"; on error, show `error` in an inline alert (mirror the rose alert style used in `session/[sessionId]/page.tsx`)
  - [x] **Client validation (AC5):** Confluence submit requires space key OR page ID; Jira `project_key` validated against `/^[A-Z][A-Z0-9_]{0,9}$/` (uppercase before test) with an inline message — do not POST invalid input
  - [x] After a successful `onComplete`, call the `useKnowledgeSources` `refetch()` so the list updates (AC4)

- [x] **Task 5: Ingested-sources list + empty/degraded states** (AC: 1, 4, 6)
  - [x] In the panel, render the `useKnowledgeSources` data as a list: source-type badge (confluence/jira), title (linked to `source_url` in a new tab when present), `ingestion_status`, `created_at`
  - [x] Loading + empty states via TanStack Query (no blank screens); empty → "No knowledge sources ingested yet."
  - [x] **AC6:** if an ingestion completes with `ingested_count === 0`, show an informational note ("Nothing was ingested — check that Pinecone and your Confluence/Jira credentials are configured.") rather than an error

### Frontend — routing + nav (AC1)

- [x] **Task 6: Add the `/knowledge` route and dashboard link** (AC: 1)
  - [x] Create `frontend/src/app/knowledge/page.tsx` rendering `KnowledgeBasePanel` (client component; consistent page chrome with the dashboard/session pages — header + "Back to Home" link)
  - [x] Add a link/button to `/knowledge` from the dashboard `frontend/src/app/page.tsx` (e.g. a "Knowledge Base" action near the session list)
  - [x] Ensure the route is auth-gated the same way other authed pages are (follow the existing pattern — the app already protects routes via Supabase session / middleware)

### Tests

- [x] **Task 7: Frontend tests** (AC: 2, 3, 5)
  - [x] `frontend/src/components/knowledge/__tests__/KnowledgeBasePanel.test.tsx` (Vitest + `@testing-library/react`, mock the `useKnowledge` hooks): Jira `project_key` invalid input blocks submit + shows message; Confluence requires space key or page ID; progress message renders while ingesting; error alert renders on `onError`; sources list renders rows and empty state
  - [x] Mock `useKnowledgeSources` to return fixture rows and assert list rendering

## Dev Notes

### Where the UI lives — decision

The knowledge base is **per-user and global**, not tied to a verification session. So a **dedicated `/knowledge` route** (linked from the dashboard) is the right home — not the session page. This keeps ingestion decoupled from the session pipeline and avoids cluttering `session/[sessionId]/page.tsx` (already large).

### The ingest hooks are ready — just render them

`useIngestConfluence`/`useIngestJira` ([useKnowledge.ts](frontend/src/lib/hooks/useKnowledge.ts)) already:
- Pull the Supabase JWT (`supabase.auth.getSession()` → `Authorization: Bearer`)
- POST to the SSE endpoint and parse `data: {...}\n\n` frames
- Dispatch `onProgress` / `onComplete(ingested_count)` / `onError(message)` and expose `{ isIngesting, error, progressMessage }`

So the component is essentially: two forms → call `ingest(request)` → reflect `isIngesting`/`progressMessage`/`error` → on complete, refetch the sources list. **Do not** re-implement SSE parsing or auth.

### New endpoint — mirror the sessions pattern

`GET /api/v1/knowledge/sources` should look like the scoped list in [sessions.py](backend/app/api/v1/sessions.py) `list_sessions`: query by `current_user`, order `created_at DESC`, `model_validate` each row. `KnowledgeSourceResponse` already exists with `from_attributes=True` — no new schema needed.

> **Test MagicMock caveat (learned in 4.4):** if you mock ORM rows with `MagicMock` and `model_validate` them, set every field the schema reads explicitly (e.g. `row.page_count = 1`, `row.source_url = None`) or Pydantic will choke on auto-Mock attributes. See [4-4 Debug Log](_bmad-output/implementation-artifacts/4-4-rag-context-display-panel.md).

### List hook uses apiClient, ingest hooks use fetch — intentional

The ingest hooks use raw `fetch` because they need a streaming `ReadableScreen` body (SSE); axios buffers the response. The **list** hook is a normal JSON GET, so use the shared `apiClient` (`lib/api/client.ts`) which already has the JWT interceptor — consistent with `useSession.ts`.

### Client-side validation must match the backend

Jira `project_key` backend rule (`schemas/knowledge.py`): `^[A-Z][A-Z0-9_]{0,9}$` after `.strip().upper()`. Validate the same in the form so users get instant feedback instead of a 422. Confluence: the endpoint 422s if both `space_key` and `page_id` are empty — block that client-side.

### Graceful degradation (AC6)

When `PINECONE_API_KEY` is unset, `_upsert_to_pinecone` no-ops and ingestion still emits `complete` with `ingested_count` reflecting DB rows — often 0. Treat `ingested_count === 0` as an informational outcome, not an error. (This is also the state on a fresh env — see the Epic 4 env note in `backend/README.md`.)

### Project Structure Notes

**Backend — modify:** `backend/app/api/v1/knowledge.py` (add `GET /sources`)
**Backend — create:** `backend/tests/test_knowledge_sources.py`
**Frontend — create:** `frontend/src/app/knowledge/page.tsx`, `frontend/src/components/knowledge/KnowledgeBasePanel.tsx`, `frontend/src/components/knowledge/__tests__/KnowledgeBasePanel.test.tsx`
**Frontend — modify:** `frontend/src/lib/hooks/useKnowledge.ts` (add `useKnowledgeSources`), `frontend/src/app/page.tsx` (dashboard link)
**Do NOT modify:** the ingest hooks' SSE/auth internals; `knowledge_service.py`; any Pinecone/embedding code

### Testing Standards

- Backend: `pytest` + `pytest-asyncio`, mock DB session per `test_sessions.py`. Run full suite for regressions; ruff-clean on new code.
- Frontend: Vitest + `@testing-library/react`; mock `@/lib/hooks/useKnowledge`. Keep changed files eslint + tsc clean. (Note: the repo's frontend suite has pre-existing failures in `GitHubSourceSelector`/`auth`/`BDDEditorPanel` unrelated to this story — don't be alarmed; scope your green-check to your files.)

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 4, Story 4.5 (FR39; NFR-R1, NFR-S8)
- [Source: frontend/src/lib/hooks/useKnowledge.ts] — ingest hooks to render (do not reinvent)
- [Source: frontend/src/lib/types/knowledge.ts] — `ConfluenceIngestRequest`, `JiraIngestRequest`, `KnowledgeSource`, `KnowledgeSSEEvent`
- [Source: backend/app/api/v1/knowledge.py] — ingest routes; add `GET /sources` here
- [Source: backend/app/schemas/knowledge.py] — `KnowledgeSourceResponse` (reuse) + Jira `project_key` regex
- [Source: backend/app/models/knowledge_source.py] — `KnowledgeSource` columns
- [Source: backend/app/api/v1/sessions.py] — scoped-list pattern to mirror for `GET /sources`
- [Source: frontend/src/lib/hooks/useSession.ts] — TanStack Query hook pattern for the list hook
- [Source: frontend/src/app/session/[sessionId]/page.tsx] — page chrome + rose error-alert style to reuse
- [Source: _bmad-output/implementation-artifacts/4-4-rag-context-display-panel.md] — MagicMock test caveat; RAG display consumes what this ingests

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- New test file `test_knowledge_sources.py` kept ruff-clean (shortened one `statement.compile()` line to avoid E501).
- Pre-existing lint/type debt in `useKnowledge.ts` (`_useSSEIngest` triggers `react-hooks/rules-of-hooks` because it's a `_`-prefixed helper calling hooks; plus a `fetch` overload tsc error) was **confirmed present before this story** via `git stash` and left untouched — the story explicitly scopes out the ingest hooks' SSE internals. My added `useKnowledgeSources` hook introduced zero new lint/type errors.

### Completion Notes List

- **Backend (AC4):** Added `GET /api/v1/knowledge/sources` to `knowledge.py` — scoped to `current_user`, newest-first, reuses the existing `KnowledgeSourceResponse` schema via `model_validate`. Mirrors the `sessions.py` list pattern; no service-layer change needed.
- **Reused, not reinvented (AC2/AC3):** The `KnowledgeBasePanel` renders the pre-built `useIngestConfluence`/`useIngestJira` hooks (SSE + Supabase auth already handled). It only supplies forms, reflects `isIngesting`/`progressMessage`/`error`, and calls `refetch()` on `onComplete`.
- **List hook (AC4):** `useKnowledgeSources` uses the shared `apiClient` (JWT interceptor) — deliberately *not* raw fetch, since it's a normal JSON GET (only the ingest streams need fetch).
- **Validation (AC5):** Jira `project_key` validated client-side against the exact backend regex `^[A-Z][A-Z0-9_]{0,9}$` (uppercased first); Confluence requires space key OR page ID. Invalid input never POSTs.
- **Degraded state (AC6):** `ingested_count === 0` renders an informational amber note ("check Pinecone/credentials"), not an error — matching the fresh-env behavior documented in `backend/README.md`.
- **Placement:** New `/knowledge` route (per-user, session-independent) linked from the dashboard top-nav, chrome consistent with `/sessions`.
- **Validation:** Backend **203 pass** (+4 new), `knowledge.py` ruff-clean. Frontend `KnowledgeBasePanel` suite **9/9**; my changed files eslint + tsc clean (only the pre-existing `useKnowledge.ts` debt remains, untouched).

### File List

**Backend — modified:**
- `backend/app/api/v1/knowledge.py` — added `GET /sources` list endpoint + imports

**Backend — created:**
- `backend/tests/test_knowledge_sources.py` — 4 tests (owner rows, empty, user-scoped query, null optional fields)

**Frontend — modified:**
- `frontend/src/lib/hooks/useKnowledge.ts` — added `useKnowledgeSources` TanStack Query hook (ingest hooks untouched)
- `frontend/src/app/page.tsx` — added "Knowledge Base" link to dashboard top-nav

**Frontend — created:**
- `frontend/src/components/knowledge/KnowledgeBasePanel.tsx` — Confluence + Jira ingest forms, progress/error/result states, validation, ingested-sources list
- `frontend/src/app/knowledge/page.tsx` — `/knowledge` route
- `frontend/src/components/knowledge/__tests__/KnowledgeBasePanel.test.tsx` — 9 tests (validation, progress, error, sources list, empty state)

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (adversarial code-review workflow) · **Date:** 2026-07-04 · **Outcome:** Approve (all findings fixed)

Git File List matched actual changes. All 6 ACs implemented; new `GET /sources` endpoint correctly user-scoped (NFR-S8, tested); client validation mirrors the backend regex.

### Action Items — all resolved

- [x] **[M1][Med]** The panel test mocked the ingest hooks wholesale, so `onComplete` was never fired — AC2 (success count), AC4 (refetch after ingest), and AC6 (degraded `count===0` note) were untested → mock now captures the passed `options`; added 2 tests firing `onComplete(5)` (asserts count note + `refetch`) and `onComplete(0)` (asserts degraded note). `[KnowledgeBasePanel.test.tsx]`
- [x] **[M2][Med]** AC4 requires showing **page count**, which the list omitted → now renders `"{page_count} page(s)"` per source, with a test. `[KnowledgeBasePanel.tsx]`
- [x] **[L1][Low]** `useKnowledgeSources` lacked `retry: false` (sibling `useSession` hooks have it) → added, so a 401 doesn't retry 3×. `[useKnowledge.ts]`

**Post-fix validation:** frontend panel suite **12 passed** (+3); changed files eslint + tsc clean (pre-existing `useKnowledge.ts` `_useSSEIngest` debt untouched, per story scope). Backend unchanged since implementation (203 pass).

## Change Log

- 2026-07-04: Implemented Story 4.5 — Knowledge Base Ingestion UI. Surfaced the previously API-only Confluence/Jira ingestion (built in 4.1/4.2) as a `/knowledge` page: two ingest forms wired to the existing hooks with live SSE progress, client-side validation, error + graceful-degradation states, and an ingested-sources list backed by a new `GET /api/v1/knowledge/sources` endpoint. Backend 203 tests pass (+4); frontend panel suite 9/9.
- 2026-07-04: Code review (adversarial) — Approve. Resolved 2 Medium + 1 Low: tested the untested `onComplete` path (success count / refetch / degraded note), rendered the missing `page_count` field (AC4), added `retry: false` to the sources hook. Frontend panel suite 12/12.
