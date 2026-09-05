# Story 2.3: LLM Verification Service & API

Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-and-rag-hardening.md)): The `/verification/run` endpoint and `run_verification` runner this story built were REMOVED with the legacy two-step flow. `verification_service` now holds only the shared substrate (Gherkin parsing, relevance selection, RAG formatting, verdict wire shape, row builder) consumed by the agentic path. Read this story as history, not as the current API surface.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user** (stubbed as `DEV_USER_ID` in this epic),
I want the system to evaluate each of my BDD scenarios against the fetched code using an LLM,
so that I get an objective per-scenario assessment of whether the code implements the acceptance criteria.

## Acceptance Criteria

1. **Given** fetched GitHub code and BDD scenarios are available for a session
   **When** `POST /api/v1/verification/run` is called with `session_id` and `bdd_content`
   **Then** the `VerificationService` parses BDD scenarios from the content, sends each to the LLM via `LLMProvider`, and streams per-scenario verdicts as SSE (FR23)

2. **And** each verdict contains: `scenario_id` (UUID), `status` (`"pass"` or `"fail"`), `justification` (natural-language referencing code), `code_reference` (object with `file`, `function`, `line` fields) (FR24)

3. **And** each verdict includes `github_links` — a list of strings referencing the repository files/lines used during analysis (FR37)

4. **And** no `rag_context` field is included — RAG enrichment is Epic 4 scope only

5. **And** for failed scenarios only, the verdict includes `implementation_suggestion` with actionable guidance for the developer (FR38)

6. **And** verdicts are streamed progressively via SSE in the format:
   `data: {"type": "verdict", "scenario_id": "...", "status": "pass"|"fail", "justification": "...", "code_reference": {...}, "github_links": [...], "implementation_suggestion": "..."|null}`
   followed by `data: {"type": "complete", "total": N, "passed": N, "failed": N}` (NFR-P5)

7. **And** the LLM is called via `LLMProvider` ABC — never directly via SDK (NFR-R4)

8. **And** verification results are persisted to a new `verification_results` table linked to `session_id` and `DEV_USER_ID`

9. **And** if the LLM call fails for any scenario, the error is emitted as `data: {"type": "error", "message": "..."}` and BDD content and session state are preserved (FR27, NFR-R3)

10. **And** full verification completes within 60 seconds for up to 15 scenarios vs up to 10,000 lines of code (NFR-P4)

11. **And** fetched files whose `path` starts with `[WARNING]` are filtered out before sending to the LLM (truncation notice artefacts from Story 2.2's `fetch_full_repo`)

12. **And** the frontend `useRunVerification()` hook connects to the SSE endpoint and emits per-verdict events into `SessionContext` as they arrive

## Tasks / Subtasks

- [x] **Task 1: Add `VerificationResult` SQLAlchemy model** (AC: 8)
  - [x] Create `backend/app/models/verification_result.py`
  - [x] Define `VerificationResult` table with columns: `id` (UUID PK), `session_id` (String FK → sessions.id), `user_id` (String), `scenario_id` (UUID), `scenario_title` (String), `status` (String: "pass"/"fail"), `justification` (Text), `code_reference` (JSON), `github_links` (JSON), `implementation_suggestion` (Text, nullable), `created_at` (DateTime)
  - [x] Import into `backend/app/models/__init__.py`

- [x] **Task 2: Create Alembic migration for `verification_results` table** (AC: 8)
  - [x] Run `alembic revision --autogenerate -m "create_verification_results_table"` inside the backend container
  - [x] Verify generated migration is correct; add it to version control

- [x] **Task 3: Extend verification Pydantic schemas** (AC: 2, 3, 5, 6)
  - [x] Edit `backend/app/schemas/verification.py`
  - [x] Add `CodeReference` model: `file: str`, `function: str`, `line: int`
  - [x] Add `VerificationRunRequest` model: `session_id: str`, `bdd_content: str`, `fetched_files: list[FetchedFile]`
  - [x] Add `VerificationVerdict` model: `scenario_id: str`, `scenario_title: str`, `status: Literal["pass", "fail"]`, `justification: str`, `code_reference: CodeReference`, `github_links: list[str]`, `implementation_suggestion: str | None = None`
  - [x] Add `VerificationCompleteEvent` model: `type: Literal["complete"]`, `total: int`, `passed: int`, `failed: int`

- [x] **Task 4: Implement `verification_service.py`** (AC: 1, 2, 3, 4, 5, 7, 9, 10, 11)
  - [x] Create `backend/app/services/verification_service.py`
  - [x] Implement `parse_bdd_scenarios(bdd_content: str) → list[dict]`
    - [x] Parse Gherkin text: extract each `Scenario:` or `Scenario Outline:` block with title and full text
    - [x] Return list of `{"id": str(uuid4()), "title": str, "text": str}`
  - [x] Implement `_build_verification_prompt(scenario: dict, fetched_files: list[FetchedFile]) → tuple[str, str]`
    - [x] System prompt: establishes LLM role as QE expert evaluating code coverage of a BDD scenario
    - [x] User prompt: includes the scenario text + all fetched file paths and contents (filtered: skip `[WARNING]` paths, truncate per-file content at 8,000 chars to keep total prompt under 100k tokens)
    - [x] Prompt must instruct LLM to return valid JSON matching the `VerificationVerdict` schema: `{"scenario_id": "...", "scenario_title": "...", "status": "pass"|"fail", "justification": "...", "code_reference": {"file": "...", "function": "...", "line": N}, "github_links": ["..."], "implementation_suggestion": "..." or null}`
  - [x] Implement async generator `run_verification(session_id: str, user_id: str, bdd_content: str, fetched_files: list[FetchedFile], llm: LLMProvider) → AsyncGenerator[str, None]`
    - [x] Filter out files whose `path` starts with `[WARNING]`
    - [x] Parse scenarios via `parse_bdd_scenarios`; if none found, yield error SSE event and return
    - [x] For each scenario: call `llm.generate_structured(prompt, system_prompt)` to get verdict dict
    - [x] Validate verdict dict against `VerificationVerdict` — if malformed, emit error event for that scenario but continue others
    - [x] Yield `data: {verdict_json}\n\n` for each scenario as it completes
    - [x] Persist each `VerificationResult` to DB via `AsyncSession` after yielding
    - [x] After all scenarios: yield `data: {"type": "complete", "total": N, "passed": M, "failed": K}\n\n`
    - [x] On `LLMProviderError`: yield `data: {"type": "error", "message": "LLM call failed: {detail}"}\n\n` and continue remaining scenarios

- [x] **Task 5: Add `POST /run` SSE endpoint to `api/v1/verification.py`** (AC: 1, 6, 9)
  - [x] Edit `backend/app/api/v1/verification.py`
  - [x] Import `StreamingResponse`, `get_db`, `llm_factory`, `verification_service`, `VerificationRunRequest`
  - [x] Implement `POST /run` handler:
    - [x] Accept `VerificationRunRequest` body
    - [x] Get `db: AsyncSession` from `Depends(get_db)`
    - [x] Get `llm: LLMProvider` from `llm_factory.get_llm_provider()`
    - [x] Use `DEV_USER_ID` from `settings.dev_user_id` (no `get_current_user` — Epic 3)
    - [x] Return `StreamingResponse(verification_service.run_verification(...), media_type="text/event-stream")`
    - [x] Error in request body → standard validation error (handled by global handler)

- [x] **Task 6: Create `useRunVerification.ts` frontend hook** (AC: 12)
  - [x] Create `frontend/src/lib/hooks/useRunVerification.ts`
  - [x] Add `VerificationVerdict` and `VerificationRunRequest` types to `frontend/src/lib/types/verification.ts`
  - [x] Implement `useRunVerification()` hook using `useSSEStream`
  - [x] Expose `runVerification(payload: VerificationRunRequest): void` — calls `useSSEStream.connect("POST", "/verification/run", payload)`
  - [x] On each `verdict` SSE event: call `setVerificationResults(prev => [...prev, verdict])`
  - [x] On `complete` event: call `setVerificationSummary({total, passed, failed})`
  - [x] On `error` event: call `setGlobalError(message)` — do NOT clear `bddContent`, `fetchedFiles`
  - [x] Expose `isVerifying` (from `useSSEStream.isStreaming`)

- [x] **Task 7: Extend `SessionContext` with verification results state** (for Story 2.4 consumption)
  - [x] Edit `frontend/src/context/SessionContext.tsx`
  - [x] Add `verificationResults: VerificationVerdict[]` + `setVerificationResults`
  - [x] Add `verificationSummary: {total: number; passed: number; failed: number} | null` + `setVerificationSummary`
  - [x] Reset both to `[]`/`null` in `handleIngestTrigger` alongside existing resets

- [x] **Task 8: Wire `onRunVerification` in the session page** (AC: 12)
  - [x] Edit `frontend/src/app/session/[sessionId]/page.tsx`
  - [x] Import `useRunVerification`; add `handleRunVerification` function
  - [x] In `handleRunVerification`: call `runVerification({session_id, bdd_content: bddContent, fetched_files: fetchedFiles})`
  - [x] Pass `onRunVerification={handleRunVerification}` and `isRunningVerification={isVerifying}` to `GitHubSourceSelector`

- [x] **Task 9: Write backend Pytest tests** (AC: 1–11)
  - [x] Create/extend `backend/tests/test_verification_service.py`
  - [x] Test `parse_bdd_scenarios`: empty string → `[]`; valid Gherkin → list of scenario dicts with correct titles
  - [x] Test `run_verification` (mock `LLMProvider.generate_structured`):
    - [x] Happy path: 2 scenarios → yields 2 verdict SSE events + complete event; verdicts persisted to DB
    - [x] Failed scenario: `status=fail` → `implementation_suggestion` is non-null string
    - [x] Passed scenario: `status=pass` → `implementation_suggestion` is null
    - [x] LLMProviderError on one scenario → error SSE event emitted; other scenarios still processed
    - [x] `[WARNING]` paths filtered out before LLM call
    - [x] Empty fetched files → still processes scenarios (LLM receives only scenario text)
    - [x] No scenarios found in BDD content → error SSE event + no DB writes
  - [x] Test `POST /run` route:
    - [x] Returns `text/event-stream` content type
    - [x] Request body validation: missing `session_id` → 422

## Dev Notes

### Epic 2 Context — DEV_USER_ID Stub

This is Epic 2 (GitHub Code Verification). **No auth middleware is applied to verification routes.** The `DEV_USER_ID` from `settings.dev_user_id` is used wherever `user_id` is needed. Do **not** add `Depends(get_current_user)` to any verification route — that comes in Epic 3.

### What Already Exists (from Stories 2.1 & 2.2)

| File | Relevant Content |
|---|---|
| `backend/app/services/github_service.py` | `GitHubServiceError`, `fetch_github_code()`, `FetchedFile` — fully implemented, do NOT modify |
| `backend/app/schemas/verification.py` | `VerificationFetchRequest`, `FetchedFile`, `VerificationFetchResponse` — extend this file, add new schemas |
| `backend/app/api/v1/verification.py` | `POST /fetch` route — extend this file with `POST /run` |
| `backend/app/api/v1/api.py` | Verification router already registered at `/verification` |
| `backend/tests/test_verification.py` | 33 passing tests — do NOT break; add new tests in a separate file |
| `frontend/src/lib/types/verification.ts` | `VerificationMode`, `FetchedFile`, `VerificationFetchResponse` — extend, do NOT rewrite |
| `frontend/src/lib/hooks/useVerification.ts` | `useVerification()` TanStack mutation (fetch step) — do not modify |
| `frontend/src/lib/hooks/useSSEStream.ts` | SSE streaming hook — **reuse this for `useRunVerification`** |
| `frontend/src/context/SessionContext.tsx` | Has `fetchedFiles: FetchedFile[]` + `bddContent: string` ready for consumption |
| `backend/app/services/llm/provider.py` | `LLMProvider` ABC with `generate()` and `generate_structured()` — use `generate_structured()` |
| `backend/app/services/llm/factory.py` | `get_llm_provider()` factory — use this to obtain the `LLMProvider` instance |
| `backend/app/core/config.py` | `settings.dev_user_id: str = "dev-stub"`, `settings.llm_provider` |
| `backend/app/models/base.py` | `Base` — all models inherit from this |
| `backend/app/models/session.py` | Session model pattern to follow for `VerificationResult` |
| `backend/alembic/versions/b37b53deea53_create_sessions_table.py` | Migration pattern to follow |

### Critical Story 2.2 Review Flag (MUST ACTION)

> [AI-Review][STORY-2.3] Filter `fetched_files` entries whose `path` starts with `[WARNING]` before passing to the LLM — these are truncation notices inserted by `fetch_full_repo`. [`backend/app/services/github_service.py:278`]

This is an explicit handoff note from the Story 2.2 code review. The `fetch_full_repo()` function may insert `FetchedFile(path="[WARNING] Tree truncated...", content="...")` entries. These must be filtered before the LLM call.

### Architecture-Mandated Patterns — ALL MUST BE FOLLOWED

1. **No business logic in route handlers** — `POST /run` handler calls `verification_service.run_verification()` and nothing else
2. **Never call LLM SDKs directly** — always via `LLMProvider` interface (`llm.generate_structured()`)
3. **FastAPI calls = TanStack React Query hooks or SSE hooks only** — never raw fetch in components
4. **No `any` type in TypeScript** — `VerificationVerdict`, `VerificationRunRequest` must be fully typed
5. **Error envelope format**: `{"error": "ERROR_CODE", "message": "...", "code": N}` — matches architecture.md lines 172–175
6. **Long-running operations must expose loader state** — `isVerifying` must be `true` while SSE stream is open
7. **Never leave a long-running action without user feedback** — progress must be visible during verification run
8. **All DB model classes inherit from `Base`** — follow pattern in `backend/app/models/session.py`

### SSE Stream Protocol

The `POST /run` endpoint returns `StreamingResponse` with `media_type="text/event-stream"`. Event types:

```
# Per-scenario verdict (streamed progressively as each scenario completes):
data: {"type": "verdict", "scenario_id": "uuid", "scenario_title": "...", "status": "pass", "justification": "...", "code_reference": {"file": "...", "function": "...", "line": 42}, "github_links": ["https://github.com/..."], "implementation_suggestion": null}

# Final summary (after all scenarios processed):
data: {"type": "complete", "total": 5, "passed": 4, "failed": 1}

# Error for a single scenario (stream continues for remaining scenarios):
data: {"type": "error", "message": "LLM call failed: rate limited"}

# Critical error (stops stream):
data: {"type": "error", "message": "No BDD scenarios found in content"}
```

Each SSE event must end with `\n\n` (two newlines) per the SSE spec.

### LLM Prompt Design

The `generate_structured()` method expects the LLM to return valid JSON. Use this prompt structure:

**System prompt (set role and constraints):**
```
You are a senior QA engineer and code reviewer. Your task is to evaluate whether a given BDD scenario is covered by the provided source code.

Respond ONLY with a valid JSON object. No explanations outside the JSON.
Schema: {
  "scenario_id": "<same as input>",
  "scenario_title": "<same as input>",
  "status": "pass" | "fail",
  "justification": "<natural-language explanation referencing specific code>",
  "code_reference": {"file": "<path>", "function": "<name>", "line": <integer>},
  "github_links": ["<file path or URL used as evidence>"],
  "implementation_suggestion": "<actionable guidance for fixing the gap>" | null
}

Rules:
- status="pass" only if the scenario's acceptance criteria is FULLY handled in the code
- status="fail" if ANY part is missing, incomplete, or incorrect
- justification MUST reference specific code (function name, variable, condition)
- implementation_suggestion MUST be non-null and actionable when status="fail"
- implementation_suggestion MUST be null when status="pass"
```

**User prompt (include scenario + code):**
```
BDD Scenario to evaluate:
Scenario ID: {scenario_id}
Title: {scenario_title}
---
{scenario_text}
---

Source code to evaluate against:
{for each file (path does NOT start with [WARNING], content truncated at 8000 chars):}
### File: {file.path}
```
{file.content[:8000]}
```
{end for}
```

### Database Schema — `verification_results` Table

```sql
CREATE TABLE verification_results (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  VARCHAR NOT NULL,  -- References sessions.id (no FK enforced in Epic 2 — auth not wired)
    user_id     VARCHAR NOT NULL,  -- DEV_USER_ID stub in Epic 2
    scenario_id UUID NOT NULL,
    scenario_title VARCHAR NOT NULL,
    status      VARCHAR(10) NOT NULL CHECK (status IN ('pass', 'fail')),
    justification TEXT NOT NULL,
    code_reference JSONB NOT NULL,  -- {"file": "...", "function": "...", "line": N}
    github_links JSONB NOT NULL,    -- ["url1", "url2"]
    implementation_suggestion TEXT,  -- NULL for pass, non-null for fail
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

In SQLAlchemy use `JSONB` type from `sqlalchemy.dialects.postgresql` for `code_reference` and `github_links`.

### LLMProvider Factory Pattern

```python
# In verification.py route handler:
from app.services.llm.factory import get_llm_provider

@router.post("/run")
async def run_verification_endpoint(
    request: VerificationRunRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    llm = get_llm_provider()
    return StreamingResponse(
        verification_service.run_verification(
            session_id=request.session_id,
            user_id=settings.dev_user_id,
            bdd_content=request.bdd_content,
            fetched_files=request.fetched_files,
            llm=llm,
            db=db,
        ),
        media_type="text/event-stream",
    )
```

### `useSSEStream` Reuse Pattern

The `useSSEStream` hook is already set up for POST SSE streams (see `frontend/src/lib/hooks/useSSEStream.ts`). It handles:
- POST with JSON payload via `connect(url, "POST", payload)`
- `"verdict"` type events → route to `onVerdict` callback (you need to extend or use `onLog` pattern)
- `"complete"` type events → existing `onComplete` handler
- `"error"` type events → existing `onError` handler

**IMPORTANT:** `useSSEStream` currently only handles `"log"`, `"complete"`, and `"error"` event types. You need to add `"verdict"` handling. Two options:
1. **Preferred**: Add `onVerdict?: (verdict: VerificationVerdict) => void` option to `useSSEStream` and handle `json.type === "verdict"` in the event loop
2. Alternative: Create a separate minimal `useVerificationSSE` hook that replicates the SSE reading logic for verdict events

Option 1 is preferred as it keeps SSE logic centralized.

### File Structure — New/Modified Files

```
backend/
  app/
    models/
      verification_result.py    ← CREATE (VerificationResult SQLAlchemy model)
      __init__.py               ← MODIFY (import VerificationResult)
    schemas/
      verification.py           ← MODIFY (add CodeReference, VerificationRunRequest, VerificationVerdict, VerificationCompleteEvent)
    services/
      verification_service.py   ← CREATE (parse_bdd_scenarios, run_verification async generator)
    api/
      v1/
        verification.py         ← MODIFY (add POST /run endpoint)
  alembic/
    versions/
      <hash>_create_verification_results_table.py  ← CREATE via alembic autogenerate

  tests/
    test_verification_service.py  ← CREATE (service-level tests)

frontend/
  src/
    lib/
      types/
        verification.ts         ← MODIFY (add VerificationVerdict, VerificationRunRequest, VerificationSummary)
      hooks/
        useSSEStream.ts         ← MODIFY (add onVerdict callback support)
        useRunVerification.ts   ← CREATE (SSE hook for /verification/run)
    context/
      SessionContext.tsx        ← MODIFY (add verificationResults, verificationSummary)
    app/
      session/[sessionId]/
        page.tsx                ← MODIFY (wire handleRunVerification, pass to UI)
```

### Previous Story Intelligence (Story 2.2 → 2.3)

Key learnings from Story 2.1 and 2.2 that apply here:

- **`@base-ui/react` not shadcn** — project uses `@base-ui/react` + native HTML + Tailwind. No shadcn Tabs or shadcn Dialog components.
- **TypeScript strict mode** — `npx tsc --noEmit` must exit 0. No `any` types. All SSE event variants must be discriminated union types.
- **Vitest is configured** for frontend tests. Pytest for backend.
- **Mobile guard pattern**: `pointer-events-none md:pointer-events-auto` used on read-only mobile views.
- **`SessionContext` extension pattern**: always extend the existing `SessionProvider` — never create a second context.
- **`JSONResponse` direct return** for standard error envelopes in route handlers (not `raise HTTPException`) — established in Story 2.2 and must be followed for any error returns in `POST /run`.
- **`verifyMutation.isPending` as single source of truth** for loading state (not dual state). Story 2.2 code review mandated replacing dual `isVerifying` state with the mutation's `.isPending`. For SSE-driven flows, `useSSEStream.isStreaming` is the equivalent single source of truth — do NOT add a separate `setIsVerifying(true)` call in the page handler.
- **Error preservation**: on any error, do NOT clear `bddContent`, `fetchedFiles`, `verificationMode`, `githubInput`, `sessionId`. Only `setGlobalError(message)` should be called.
- **`fetch_full_repo` truncation artefacts**: files with `path.startsWith("[WARNING]")` must be filtered before the LLM call. This is an explicit handoff from Story 2.2's code review.

### Git Intelligence (Recent Commits)

From commit `90f8688` (most recent):
- All 12 files from Story 2.2 are now in the repo
- `verification_service.py` does **not** exist yet — this is a net-new file for Story 2.3
- `verification_results` DB table does **not** exist yet — migration needed
- `useRunVerification.ts` does **not** exist yet — net-new
- `backend/app/models/` only has `base.py` and `session.py` — `verification_result.py` is net-new
- `backend/app/schemas/verification.py` exists with `VerificationFetchRequest`, `FetchedFile`, `VerificationFetchResponse` — extend this file

### Limitations / Out of Scope for This Story

- No RAG context (`rag_context` field) — that is Epic 4 scope
- No real auth (`Depends(get_current_user)`) — that is Epic 3. Use `settings.dev_user_id`
- No streaming token-by-token output from LLM — LLM is called synchronously per scenario via `generate_structured()` and the verdict is yielded atomically over SSE once the LLM responds
- No UI for displaying results — Story 2.4 renders the `VerificationResultRow` components. This story only sends results to `SessionContext`
- No GitHub OAuth — PAT from `settings.github_pat`
- No traceability report generation — Epic 5

### References

- Epic 2 Story 2.3 definition: [epics.md](../_bmad-output/planning-artifacts/epics.md) lines 486–508
- Architecture — Verification domain: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 37–38, 248–250
- Architecture — Mandatory rules: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 305–316
- Architecture — Error envelope format: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 172–175
- Architecture — LLM provider abstraction: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 186–196
- Architecture — Verification result format: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 291–303
- Previous story (2.2) dev notes: [2-2-github-code-fetching-all-3-modes.md](2-2-github-code-fetching-all-3-modes.md)
- LLMProvider ABC: [backend/app/services/llm/provider.py](../../backend/app/services/llm/provider.py)
- LLMProvider factory: [backend/app/services/llm/factory.py](../../backend/app/services/llm/factory.py)
- SSE hook: [frontend/src/lib/hooks/useSSEStream.ts](../../frontend/src/lib/hooks/useSSEStream.ts)
- Existing schema patterns: [backend/app/schemas/verification.py](../../backend/app/schemas/verification.py)
- Session model pattern: [backend/app/models/session.py](../../backend/app/models/session.py)
- Config (dev_user_id, llm_provider): [backend/app/core/config.py](../../backend/app/core/config.py)
- Existing verification route: [backend/app/api/v1/verification.py](../../backend/app/api/v1/verification.py)
- SessionContext: [frontend/src/context/SessionContext.tsx](../../frontend/src/context/SessionContext.tsx)
- Verification types: [frontend/src/lib/types/verification.ts](../../frontend/src/lib/types/verification.ts)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

_No blocking issues encountered._

### Completion Notes List

- Implemented `parse_bdd_scenarios` using regex on `Scenario:` / `Scenario Outline:` blocks; each scenario gets a generated UUID.
- `_build_verification_prompt` applies the story-specified system prompt and builds a user prompt with per-file content truncated at 8,000 chars; `[WARNING]` paths skipped.
- `run_verification` async generator filters `[WARNING]` files, iterates scenarios, yields SSE `verdict` events, persists each `VerificationResult` to DB via `db.flush()` after yielding, then yields a `complete` event. Per-scenario `LLMProviderError` yields an error SSE and continues; malformed LLM response yields error and continues.
- Alembic migration `a9d4e72f1c83` created manually (no DB connection available in dev) following the `b37b53deea53` pattern; uses `postgresql.JSONB` for `code_reference` and `github_links`.
- `useSSEStream` extended with `onVerdict` and `onVerificationComplete` callbacks, discriminating `complete` events by `"session_id" in json` vs `"total" in json`.
- `useRunVerification` uses `isStreaming` from `useSSEStream` as the single source of truth for `isVerifying` — no duplicate state per Story 2.2 code-review mandate.
- `GitHubSourceSelector` extended with `onRunVerification` / `isRunningVerification` props; "Run LLM Verification" button appears when `fetchedFiles.length > 0`.
- All 82 backend tests pass (17 new + 65 existing, no regressions).

### File List

**Backend — created:**
- `backend/app/models/verification_result.py`
- `backend/app/services/verification_service.py`
- `backend/alembic/versions/a9d4e72f1c83_create_verification_results_table.py`
- `backend/tests/test_verification_service.py`

**Backend — modified:**
- `backend/app/models/__init__.py`
- `backend/app/schemas/verification.py`
- `backend/app/api/v1/verification.py`

**Frontend — created:**
- `frontend/src/lib/hooks/useRunVerification.ts`

**Frontend — modified:**
- `frontend/src/lib/types/verification.ts`
- `frontend/src/lib/hooks/useSSEStream.ts`
- `frontend/src/context/SessionContext.tsx`
- `frontend/src/app/session/[sessionId]/page.tsx`
- `frontend/src/components/pipeline/GitHubSourceSelector.tsx`

**Sprint tracking — modified:**
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
