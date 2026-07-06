# Story 3.5: Session History Dashboard

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want to view a list of my past sessions and open any of them to review their BDD and results,
So that I can audit, reference, and continue work on previously processed tickets.

## Acceptance Criteria

1. **Given** the user navigates to `/` (dashboard)
   **When** the `useSessionList()` TanStack Query hook fetches `GET /api/v1/sessions`
   **Then** a list of the user's past sessions is displayed, each showing Jira ticket ID, creation date, and BDD generation status (FR32)

2. **Given** the user clicks a past session row
   **When** the session detail page (`/session/[sessionId]`) loads
   **Then** the BDD editor is populated with the saved BDD content from that session (FR33)

3. **And** if a verification report exists for the session it is also rendered in the results panel

4. **And** sessions belonging to other users are never returned by the API — enforced by FastAPI `user_id` check + Supabase RLS (FR34)

5. **And** TanStack Query handles loading and error states — no blank screens without feedback

## Tasks / Subtasks

### Backend Tasks

- [x] **Task 1: Add `bdd_status` to `SessionResponse`** (AC: 1)
  - [x] In `backend/app/schemas/session.py`, add `bdd_status: str = "none"` to `SessionResponse`
  - [x] Valid values: `"none"` (no BDD rows), `"generated"`, `"uploaded"` (latest `bdd_files.source` value)

- [x] **Task 2: Enhance `GET /api/v1/sessions` with BDD status** (AC: 1)
  - [x] In `backend/app/api/v1/sessions.py`, after loading sessions, query `bdd_files` for each session_id
  - [x] Build a `{session_id: source}` map using Python-level dedup (ORDER BY created_at DESC, first row per session_id wins)
  - [x] Build a `{session_id: source}` map and inject into `SessionResponse` as `bdd_status`
  - [x] Maintain user isolation: only query `bdd_files` rows matching the current user's sessions

- [x] **Task 3: New endpoint `GET /api/v1/sessions/{session_id}/bdd`** (AC: 2)
  - [x] Add to `backend/app/api/v1/sessions.py` (keeps session-scoped resources together)
  - [x] Query `bdd_files` for the session ordered by `created_at DESC LIMIT 1`
  - [x] Enforce ownership: load the `Session` row first, verify `session.user_id == current_user` → 403 if mismatch
  - [x] Return 404 if no session or no `bdd_files` row
  - [x] Response body: `SessionBDDResponse`

- [x] **Task 4: New endpoint `GET /api/v1/sessions/{session_id}/verification-results`** (AC: 3)
  - [x] Add to `backend/app/api/v1/sessions.py`
  - [x] Enforce ownership: verify `session.user_id == current_user` → 403
  - [x] Query `verification_results` for the session ordered by `created_at ASC`
  - [x] Return empty list `[]` if no results (not 404)
  - [x] Response body: `list[StoredVerificationResult]`

- [x] **Task 5: Add new schemas** (AC: 2, 3)
  - [x] Added `SessionBDDResponse` and `StoredVerificationResult` to `backend/app/schemas/session.py`

- [x] **Task 6: Register new routes in FastAPI router** (AC: 2, 3)
  - [x] New routes resolve via existing sessions router mounted at `/api/v1/sessions`

- [x] **Task 7: Backend tests** (AC: 1, 2, 3, 4)
  - [x] `GET /sessions` returns `bdd_status="none"` when no `bdd_files` exist
  - [x] `GET /sessions` returns `bdd_status="generated"` when generated bdd_files row exists
  - [x] `GET /sessions` returns `bdd_status="uploaded"` when uploaded bdd_files row exists
  - [x] `GET /sessions/{id}/bdd` returns `SessionBDDResponse` for owner (200)
  - [x] `GET /sessions/{id}/bdd` returns 403 for non-owner
  - [x] `GET /sessions/{id}/bdd` returns 404 when session not found
  - [x] `GET /sessions/{id}/bdd` returns 404 when no bdd_files exist
  - [x] `GET /sessions/{id}/verification-results` returns results list for owner (200)
  - [x] `GET /sessions/{id}/verification-results` returns empty list when none
  - [x] `GET /sessions/{id}/verification-results` returns 403 for non-owner
  - [x] All 137 backend tests passing, zero regressions

### Frontend Tasks

- [x] **Task 8: Add `SessionListItem` and related types** (AC: 1)
  - [x] Added `SessionListItem`, `SessionBDDResponse`, `StoredVerificationResult` to `frontend/src/lib/types/session.ts`

- [x] **Task 9: Create `frontend/src/lib/hooks/useSession.ts`** (AC: 1, 2, 3, 5)
  - [x] `useSessionList()` — TanStack Query hook for `GET /api/v1/sessions`
  - [x] `useSessionBDD(sessionId)` — hook for `GET /api/v1/sessions/{id}/bdd`, disabled when null, no retry
  - [x] `useSessionVerificationResults(sessionId)` — hook for `GET /api/v1/sessions/{id}/verification-results`

- [x] **Task 10: Update `frontend/src/app/page.tsx` (dashboard with session history)** (AC: 1, 5)
  - [x] `useSessionList()` call with loading/error/empty states
  - [x] Session history table with Jira Ticket ID, Created date, BDD Status columns
  - [x] `BddStatusBadge` component: green (Generated), blue (Uploaded), grey (None)
  - [x] Clickable rows navigate to `/session/{id}`
  - [x] TypeScript and ESLint clean

- [x] **Task 11: Update `frontend/src/app/session/[sessionId]/page.tsx` to load existing session** (AC: 2, 3, 5)
  - [x] `useSessionBDD(routeSessionId)` populates BDD editor on mount
  - [x] Handles `source="generated"` (JSON parse → `scenariosToGherkin`) and `source="uploaded"` (raw content) 
  - [x] `useSessionVerificationResults(routeSessionId)` maps to `VerificationVerdict[]` and populates `SessionContext`
  - [x] Guards prevent overwriting active session data (sessionId check, verificationResults.length check)
  - [x] TypeScript strict — no `any`, double cast via `unknown` for `code_reference`
  - [x] TypeScript and ESLint clean

## Dev Notes

### Critical: `bdd_files.content` Format Differs by Source

The `bdd_files.content` column stores different formats depending on `source`:

| `source` | `content` format | Frontend action |
|---|---|---|
| `"generated"` | JSON string of `BDDGenerateResponse` (has `scenarios: BDDScenario[]`) | `JSON.parse(content).scenarios` → `scenariosToGherkin(scenarios)` |
| `"uploaded"` | Raw Gherkin feature file text | Use `content` directly as `bddContent` |

This is set in `backend/app/api/v1/bdd.py`:
- Generate endpoint: `content=response.model_dump_json()` [bdd.py:~55]
- Upload endpoint: `content=content` (UTF-8 decoded bytes) [bdd.py:~115]

### Backend: Efficient BDD Status Query

The simplest correct approach for adding `bdd_status` to `GET /api/v1/sessions`:

```python
# After loading sessions, get the latest bdd source per session_id
session_ids = [str(s.id) for s in sessions]
bdd_rows_result = await db.execute(
    select(BddFile.session_id, BddFile.source, BddFile.created_at)
    .where(BddFile.session_id.in_(session_ids))
    .order_by(BddFile.created_at.desc())
)
bdd_map: dict[str, str] = {}
for row in bdd_rows_result.all():
    if row.session_id not in bdd_map:  # first = newest due to ORDER BY created_at DESC
        bdd_map[row.session_id] = row.source
```

### Architecture Compliance Checklist

- [x] Route handlers in `sessions.py` contain no business logic — only DB reads + ownership checks
- [x] All DB queries scoped to `current_user` (via session ownership verification)
- [x] No raw `fetch`/`axios` calls in React components — only hooks consumed
- [x] Loading and error states handled for all TanStack Query hooks (no blank screens)
- [x] Types in `session.ts` use snake_case fields matching backend JSON output
- [x] No `any` in TypeScript — `unknown` used for untyped JSON fields

### References

- Story 3.5 AC: [epics.md](/_bmad-output/planning-artifacts/epics.md#story-35-session-history-dashboard)
- Session API routes: [sessions.py](backend/app/api/v1/sessions.py)
- SessionResponse schema: [schemas/session.py](backend/app/schemas/session.py)
- BddFile model: [models/bdd_file.py](backend/app/models/bdd_file.py)
- VerificationResult model: [models/verification_result.py](backend/app/models/verification_result.py)
- BDD API (content format reference): [api/v1/bdd.py](backend/app/api/v1/bdd.py)
- Dashboard page: [app/page.tsx](frontend/src/app/page.tsx)
- Session pipeline page: [app/session/[sessionId]/page.tsx](frontend/src/app/session/[sessionId]/page.tsx)
- SessionContext: [context/SessionContext.tsx](frontend/src/context/SessionContext.tsx)
- API client (auth interceptor): [lib/api/client.ts](frontend/src/lib/api/client.ts)
- Session types: [lib/types/session.ts](frontend/src/lib/types/session.ts)
- BDD generate hook (scenariosToGherkin): [lib/hooks/useBDDGenerate.ts](frontend/src/lib/hooks/useBDDGenerate.ts)
- Previous story (3.4 handoff): [3-4-supabase-storage-setup.md](_bmad-output/implementation-artifacts/3-4-supabase-storage-setup.md)
- FR32: session history dashboard, FR33: session retrieval view, FR34: RLS enforcement

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- Fixed Pydantic validation error: `SessionResponse.model_validate(orm_obj)` reads `bdd_status` from MagicMock auto-attr → switched to explicit `SessionResponse(id=..., bdd_status=...)` constructor in both `list_sessions` and `get_session` routes
- Fixed test `test_list_sessions_returns_only_current_users_sessions`: updated from single-execute mock to two-execute side_effect (list_sessions now makes 2 DB calls)

### Completion Notes List

- Added `bdd_status: str = "none"` to `SessionResponse`; added `SessionBDDResponse` and `StoredVerificationResult` schemas to `backend/app/schemas/session.py`
- Enhanced `GET /api/v1/sessions`: queries `bdd_files` for all session IDs in a single query, deduplicates in Python (newest first), injects `bdd_status` via explicit `SessionResponse` constructor
- Added `GET /api/v1/sessions/{session_id}/bdd`: ownership check → latest `bdd_files` row → returns `SessionBDDResponse`; 403 for non-owner, 404 if no session or no BDD
- Added `GET /api/v1/sessions/{session_id}/verification-results`: ownership check → all `VerificationResult` rows for session ordered ASC; returns `[]` for no results
- Created `frontend/src/lib/hooks/useSession.ts` with three TanStack Query hooks: `useSessionList`, `useSessionBDD`, `useSessionVerificationResults`
- Updated `frontend/src/lib/types/session.ts`: added `SessionListItem`, `SessionBDDResponse`, `StoredVerificationResult` (existing types preserved)
- Updated `frontend/src/app/page.tsx`: session history table with loading/empty/error states, BDD status badges (Generated/Uploaded/None), clickable rows to `/session/{id}`
- Updated `frontend/src/app/session/[sessionId]/page.tsx`: two `useEffect` hooks load existing BDD (JSON parse for generated, raw for uploaded) and verification results (mapped to `VerificationVerdict[]`) — guards prevent overwriting active session
- 137 backend tests passing (16 session tests, 121 pre-existing), zero regressions; TypeScript strict + ESLint clean

### File List

backend/app/schemas/session.py (modified — added bdd_status field, SessionBDDResponse, StoredVerificationResult)
backend/app/api/v1/sessions.py (modified — enhanced list_sessions with bdd_status, added get_session_bdd + get_session_verification_results)
backend/tests/test_sessions.py (modified — expanded from 7 to 16 tests covering new endpoints and bdd_status)
frontend/src/lib/types/session.ts (modified — added SessionListItem, SessionBDDResponse, StoredVerificationResult)
frontend/src/lib/hooks/useSession.ts (new — useSessionList, useSessionBDD, useSessionVerificationResults)
frontend/src/app/page.tsx (modified — added session history table with TanStack Query, BDD status badges)
frontend/src/app/session/[sessionId]/page.tsx (modified — added BDD + verification results load on mount)

## Change Log

- 2026-04-11: Implemented Story 3.5 — Session History Dashboard. Enhanced sessions API with bdd_status, added /bdd and /verification-results sub-routes, created useSession hooks, updated dashboard page with session history table, updated pipeline page to load existing session data. 137 tests passing.
- 2026-04-11: Code review fixes applied — (H1) Added loading banner while existingBDD loads on session page (AC5); (H2) Added missing 404 test for GET /sessions/{id}/verification-results; (H3) Added useSession hook + effect to restore jiraTicketId from saved session; (M1) bdd_status changed to Literal type in SessionResponse schema; (M2) Added retry:false to useSessionVerificationResults; (M3) Fixed GET /sessions/{id} to query bdd_files and return accurate bdd_status; (M4) Fixed unsafe app.dependency_overrides.clear() to save/restore. 138 tests passing.
